# Hypo-Agent 渠道系统审查报告

审查时间：2026-03-27  
审查范围：消息渠道系统当前工作区代码（不是只看 `HEAD`）  
审查方式：静态阅读 + 最小化运行验证 + 定向测试

本次审查基于当前工作树。需要先说明两点：

1. 当前工作树不是干净状态，渠道相关文件存在未提交改动，且有未跟踪的新文件，包括：
   - `src/hypo_agent/core/platform_message_preparation.py`
   - `src/hypo_agent/gateway/app.py`
   - `src/hypo_agent/gateway/dashboard_api.py`
   - `web/src/views/DashboardView.vue`
   - `src/hypo_agent/channels/qq_bot_channel.py`（未跟踪）
   - `src/hypo_agent/gateway/qqbot_ws_client.py`（未跟踪）
2. 因此，下面的“回归引入时间”有两类：
   - 可以从已提交历史明确定位到 commit
   - 只能定位到“当前工作区未提交改动”

补充验证：

- 已运行：
  - `uv run pytest tests/core/test_markdown_splitter.py tests/core/test_platform_message_preparation.py tests/core/test_qq_text_renderer.py tests/gateway/test_webui_qq_sync.py tests/gateway/test_channels_status_api.py -q`
- 结果：
  - 36 个测试全部通过
- 结论：
  - 当前测试集已经把一部分“现状行为”固化了，但并没有覆盖历史设计目标，尤其没有覆盖 QQ Bot 路径下的富文本/来源标签正确性。

---

## 第一部分：渠道系统架构梳理

### 0. 先说结论

当前“消息渠道系统”已经不是单一链路，而是三套并行且部分重叠的链路：

- WebUI 链路：`gateway/ws.py` + `ChatPipeline` + `WebUIAdapter`
- 旧 QQ（NapCat / OneBot）链路：`QQChannelService` + `QQAdapter`
- 新 QQ（官方 QQ Bot）链路：`QQBotChannelService`，绕过 `QQAdapter`
- 微信链路：`WeixinChannel` + `WeixinAdapter`，但内部复用了 `QQAdapter` 做文本/块拆分

`src/hypo_agent/core/formatting.py` 在当前代码树中不存在；实际共享格式化逻辑已经分散到：

- `src/hypo_agent/core/platform_message_preparation.py`
- `src/hypo_agent/core/qq_text_renderer.py`
- `src/hypo_agent/channels/qq_adapter.py`
- `src/hypo_agent/channels/qq_bot_channel.py`
- `src/hypo_agent/channels/weixin/weixin_adapter.py`

### 1. QQ 消息发送链路

当前实际有两条 QQ 发送链路。

#### 1.1 当前优先链路：QQ Bot 官方通道

实际选择点：

- `src/hypo_agent/gateway/app.py:1161-1246` `refresh_qq_channel_service()`
- 如果 `services.qq_bot` 可用，会优先实例化 `QQBotChannelService`，并把它注册为 `channel_dispatcher` 的 `"qq"` sink

数据流：

`ChatPipeline.stream_reply()`  
→ `ChatPipeline._broadcast_message()` (`src/hypo_agent/core/pipeline.py:1212-1243`)  
→ `app.on_proactive_message()` (`src/hypo_agent/gateway/app.py:1398-1424`)  
→ `ChannelDispatcher.broadcast()` (`src/hypo_agent/core/channel_dispatcher.py:28-57`)  
→ 已注册 sink `"qq"`  
→ `QQBotChannelService.send_message()` (`src/hypo_agent/channels/qq_bot_channel.py:287-330`)

QQ Bot 内部继续分流：

`Message`  
→ `prepare_message_for_platform(message, platform="qq")` (`src/hypo_agent/channels/qq_bot_channel.py:301`)  
→ `_downgrade_markdown()` (`src/hypo_agent/channels/qq_bot_channel.py:86-110`)  
→ `_resolve_image_source()` (`src/hypo_agent/channels/qq_bot_channel.py:470-482`)  
→ 有图时 `_send_image_with_fallback()` (`src/hypo_agent/channels/qq_bot_channel.py:511-542`)  
→ 最终 `_send_with_retry()` (`src/hypo_agent/channels/qq_bot_channel.py:644-718`)  
→ QQ 官方 API `/v2/users/{openid}/messages`

关键变量：

- `prepared_message`
- `text`
- `image_source`
- `fallback_text`
- `route_kind`, `openid`, `guild_id`, `msg_id`

关键事实：

- 这条链路**不经过** `QQAdapter.format()`
- 这条链路**不生成** `CQ:image` / `CQ:text` 混排段
- 这条链路**不调用** `ImageRenderer.render_to_image()` 去把代码块/表格块渲染成图片

#### 1.2 旧链路：NapCat / OneBot 回退通道

只在 QQ Bot 未启用时使用。

数据流：

`ChatPipeline.stream_reply()`  
→ `ChatPipeline._broadcast_message()`  
→ `app.on_proactive_message()`  
→ `ChannelDispatcher.broadcast()`  
→ `"qq"` sink  
→ `QQChannelService.send_message()` (`src/hypo_agent/channels/qq_channel.py:94-112`)  
→ `_prefixed_message()` (`src/hypo_agent/channels/qq_channel.py:236-247`)  
→ `QQAdapter.send_message()` (`src/hypo_agent/channels/qq_adapter.py:133-137`)  
→ `QQAdapter.format()` (`src/hypo_agent/channels/qq_adapter.py:38-76`)  
→ `split_markdown_blocks()` (`src/hypo_agent/core/markdown_splitter.py:10-66`)

块处理细节：

- 文本块：
  - `render_qq_plaintext()` (`src/hypo_agent/core/qq_text_renderer.py:17-68`)
  - 生成 `{"type": "text", "data": {"text": ...}}`
- `table` / `code` / `math` / `mermaid`：
  - `renderable_markdown_block()` (`src/hypo_agent/core/markdown_splitter.py:69-78`)
  - `image_renderer.render_to_image(..., block_type=...)` (`src/hypo_agent/channels/qq_adapter.py:62-67`)
  - 生成 `{"type": "image", "data": {"file": ...}}`
- 附件：
  - `_attachment_segment()` (`src/hypo_agent/channels/qq_adapter.py:288-296`)

最终发送：

`QQAdapter.send_private_segments()` (`src/hypo_agent/channels/qq_adapter.py:139-150`)  
→ NapCat HTTP `/send_private_msg`

这条链路才是历史设计目标里的“QQ 混排消息段链路”。

### 2. 微信消息发送链路

入口注册：

- `src/hypo_agent/gateway/app.py:1291-1314` `start_weixin_channel()`
- 注册 sink：`app.state.channel_dispatcher.register("weixin", adapter.push)`

数据流：

`ChatPipeline.stream_reply()`  
→ `ChatPipeline._broadcast_message()`  
→ `app.on_proactive_message()`  
→ `ChannelDispatcher.broadcast()`  
→ `"weixin"` sink  
→ `WeixinAdapter.push()` (`src/hypo_agent/channels/weixin/weixin_adapter.py:60-153`)

微信内部处理：

`Message`  
→ `prepare_message_for_platform(message, platform="weixin")` (`src/hypo_agent/channels/weixin/weixin_adapter.py:76`)  
→ 如果是“单图片消息”，直接 `_send_attachment_image()` / `_send_image_reference()` (`src/hypo_agent/channels/weixin/weixin_adapter.py:215-279`)  
→ 否则先 `_prepend_source_prefix()` (`src/hypo_agent/channels/weixin/weixin_adapter.py:333-346`)  
→ 再调用内部 `_formatter = QQAdapter(...)`  
→ `await self._formatter.format(rendered_message)` (`src/hypo_agent/channels/weixin/weixin_adapter.py:131`)  
→ 对每个 segment：
  - `text` → `_send_text_chunk()` (`src/hypo_agent/channels/weixin/weixin_adapter.py:417-437`)
  - `image` → `_send_image_reference()` (`src/hypo_agent/channels/weixin/weixin_adapter.py:228-280`)
  - `file` → 降级成 `[文件] xxx` 文本 (`src/hypo_agent/channels/weixin/weixin_adapter.py:206-213`)

关键事实：

- 微信链路虽然目标平台是微信，但实际仍复用 `QQAdapter.format()` 做块拆分和代码块转图片
- 微信的“纯文本 + 独立图片分批发送”能力是通过 `prepare_message_for_platform()` + `QQAdapter.format()` + `_send_segment()` 共同拼出来的

### 3. 跨渠道广播链路

#### 3.1 WebUI 入站

数据流：

`gateway/ws.py:websocket_chat()` (`src/hypo_agent/gateway/ws.py:142-189`)  
→ 构造 `Message(channel="webui", metadata.webui_client_id=...)` (`src/hypo_agent/gateway/ws.py:157-165`)  
→ `broadcast_message(inbound.model_dump(...), exclude_client_ids={sender})` 直接广播给其他 WebUI 客户端 (`src/hypo_agent/gateway/ws.py:168-171`)  
→ `mirror_webui_message_to_qq(inbound)` (`src/hypo_agent/gateway/ws.py:172-176`)  
→ `pipeline.stream_reply(inbound)` (`src/hypo_agent/gateway/ws.py:177-179`)

AI 回复继续走：

`ChatPipeline.stream_reply()`  
→ `ChatPipeline._broadcast_message()`  
→ `app.on_proactive_message()`  
→ `ChannelDispatcher.broadcast()`  
→ WebUI / 微信  
→ `mirror_webui_message_to_qq()` 额外再走一次 QQ

#### 3.2 QQ 入站

当前主链路是 QQ Bot：

`QQBotWebSocketClient._handle_payload()` (`src/hypo_agent/gateway/qqbot_ws_client.py:132-177`)  
→ `QQBotChannelService.handle_event()` (`src/hypo_agent/channels/qq_bot_channel.py:218-270`)  
→ 构造 `Message(channel="qq", session_id="main")` (`src/hypo_agent/channels/qq_bot_channel.py:237-253`)  
→ `pipeline.on_proactive_message(inbound, exclude_channels={"qq"})` (`src/hypo_agent/channels/qq_bot_channel.py:255-262`)  
→ 先把用户消息广播到其他渠道  
→ `pipeline.enqueue_user_message(inbound, emit=...)` (`src/hypo_agent/channels/qq_bot_channel.py:460-465`)  
→ pipeline 生成回复  
→ 回复再经 `app.on_proactive_message()` 广播到 WebUI / 微信 / QQ 本身

旧 NapCat 链路类似：

`NapCatWebSocketClient.run_once()` (`src/hypo_agent/gateway/qq_ws_client.py:62-88`)  
→ `QQChannelService.handle_onebot_event()` (`src/hypo_agent/channels/qq_channel.py:60-89`)

#### 3.3 微信入站

`WeixinChannel._poll_loop()` (`src/hypo_agent/channels/weixin/weixin_channel.py:93-122`)  
→ `_handle_message()` (`src/hypo_agent/channels/weixin/weixin_channel.py:123-232`)  
→ 构造 `Message(channel="weixin", session_id="main")` (`src/hypo_agent/channels/weixin/weixin_channel.py:200-212`)  
→ `pipeline.on_proactive_message(message, exclude_channels={"weixin"})` (`src/hypo_agent/channels/weixin/weixin_channel.py:214-221`)  
→ 先广播用户消息  
→ `queue.put({"event_type": "user_message", ...})` (`src/hypo_agent/channels/weixin/weixin_channel.py:223-229`)  
→ pipeline 回复  
→ 回复再经 `app.on_proactive_message()` 广播到其它渠道

#### 3.4 来源标签实际添加位置

当前不是单点添加，而是散落在多处：

- WebUI → QQ：
  - `src/hypo_agent/gateway/app.py:1071-1084` `mirror_webui_message_to_qq()`
  - 这里加的是 `[WebUI] User:` / `[WebUI] Assistant:`
- 微信 → 旧 QQ：
  - `src/hypo_agent/channels/qq_channel.py:236-247` `_prefixed_message()`
  - 这里只认 `"weixin": "[微信] "`
- QQ / WebUI → 微信：
  - `src/hypo_agent/channels/weixin/weixin_adapter.py:333-346` `_prepend_source_prefix()`
  - 这里认 `[WebUI] ` / `[QQ] `
- QQ Bot 出站：
  - `src/hypo_agent/channels/qq_bot_channel.py:287-330`
  - **没有统一来源标签注入**

结论：

- 来源标签没有统一策略层
- 当前标签依赖“发往哪个平台”以及“走哪条 QQ 后端”

### 4. 前端 Dashboard 数据流

后端：

`GET /api/channels/status`  
→ `src/hypo_agent/gateway/dashboard_api.py:163-308` `channels_status()`

前端：

`DashboardView.vue`  
→ `loadChannels()` (`web/src/views/DashboardView.vue:686-689`)  
→ `channels.value = response.channels`  
→ `channelCardMap` (`web/src/views/DashboardView.vue:382-435`)  
→ `ChannelStatusCard.vue` (`web/src/components/dashboard/ChannelStatusCard.vue:23-43`)

系统状态卡不是同一路：

`GET /api/dashboard/status`  
→ `src/hypo_agent/gateway/dashboard_api.py:138-160`  
→ `DashboardView.vue:663-684`

---

## 第二部分：问题定位

## A. QQ 图片被自动降级，应该发图片的内容变成纯文本或丢失

### 定位

- `src/hypo_agent/gateway/app.py:1184-1210` `refresh_qq_channel_service()`
- `src/hypo_agent/channels/qq_bot_channel.py:287-330` `QQBotChannelService.send_message()`
- `src/hypo_agent/channels/qq_bot_channel.py:470-482` `_resolve_image_source()`
- `src/hypo_agent/core/platform_message_preparation.py:21-81` `prepare_message_for_platform()`

### 直接原因

1. 当前工作区里，QQ 出站主路径已经从 `QQAdapter.format()` 切到了 `QQBotChannelService.send_message()`。
2. `QQBotChannelService.send_message()` 只会处理两类东西：
   - 文本：`_downgrade_markdown()`
   - 已存在的单张图片：`_resolve_image_source()`
3. 代码块 / 表格 / 公式 / Mermaid 这些“原本应该先渲染成图片”的内容，在 QQ Bot 路径里没有任何地方调用 `ImageRenderer.render_to_image()`。
4. 当前工作区里 `prepare_message_for_platform()` 还把 `"qq"` 也纳入了微信式拆分规则，会把 inline image 改写成“文本占位 + 独立图片消息”，进一步破坏了历史 QQ 混排契约。

### 最小化验证

当前代码验证结果：

```text
1 图在这里 【见下方图片】 []
2 None ['image:./cat.png']
```

即 `prepare_message_for_platform(..., "qq")` 已经实际把 QQ 文本拆成了占位文本和独立图片。

### 回归引入时间

- 已提交历史里的拐点：`a166ea3`（2026-03-24，`M14: add Weixin channel and platform-specific rich message preparation`）引入了平台预处理层。
- 当前直接回归不在 `git log` 的已提交历史里，而在**当前工作区未提交改动**：
  - `platform_message_preparation.py` 从“只处理微信”改成了“处理 `weixin/qq/qq_bot`”
  - `qq_bot_channel.py` / `qqbot_ws_client.py` 仍是未跟踪文件，说明 QQ Bot 迁移目前处于未完整提交状态。

### 修复方向

恢复 QQ 专属富文本输出链路；`prepare_message_for_platform()` 只保留微信/纯批量平台的拆分语义，不要让 QQ 走微信式拆分。

## B. Markdown 表格渲染失效，渲染出来的是表格源码而不是表格图片

### 定位

- `src/hypo_agent/core/markdown_splitter.py:100-124` `_read_table_block()`
- `src/hypo_agent/channels/qq_adapter.py:54-70` `QQAdapter.format()`
- `src/hypo_agent/templates/render.html:259-264` `renderContent()` 的 `table` 分支

### 直接原因

主因不在 `render.html`，而在 table block 根本没被识别出来：

1. `_read_table_block()` 要求每一行都满足 `startswith("|") and endswith("|")`。
2. 这会漏掉大量标准 GFM 表格，例如：

```md
A | B
--- | ---
1 | 2
```

3. 一旦表格没有被识别成 `type="table"`，它就会留在 `text` block 里。
4. 之后 QQ 路径不会进入 `image_renderer.render_to_image()`，而是把源码当普通文本发出。

### 最小化验证

当前代码验证结果：

```python
from hypo_agent.core.markdown_splitter import split_markdown_blocks
split_markdown_blocks("A | B\n--- | ---\n1 | 2\n")
# => [{'type': 'text', 'content': 'A | B\n--- | ---\n1 | 2\n'}]
```

### 关于 `render.html`

`render.html` 的 table 分支本身是调用 `marked.parse(String(content))` 的：

- `src/hypo_agent/templates/render.html:259-262`

所以当前并不是“直接把原始 markdown 字符串塞进 HTML”。  
但它还有一个次级风险：

- `src/hypo_agent/templates/render.html:260-264`
- 如果 CDN 加载的 `marked` 在 1.5s 内没就绪，会回退成 `<pre>`，仍然显示源码。

也就是说：

- 主回归：table 没被 splitter 识别
- 次回归风险：renderer 对 CDN/超时过于敏感

### 回归引入时间

- `63cfad7`（2026-03-20，`M13: multimodal vision input + image rendering + QQ rich output + ExportSkill`）
- 该 commit 同时引入了 `markdown_splitter.py`、`image_renderer.py`、`render.html`

### 修复方向

把 table 识别升级到完整 GFM 语法，并让表格渲染不依赖远端 CDN 的 `marked` 超时窗口。

## C. QQ 文本降级器似乎失效，`-` 列表不转黑点、Markdown 符号未清理

### 定位

- `src/hypo_agent/core/qq_text_renderer.py:17-68` `render_qq_plaintext()`
- `src/hypo_agent/channels/qq_adapter.py:54-76` `QQAdapter.format()`
- `src/hypo_agent/channels/qq_bot_channel.py:86-110` `_downgrade_markdown()`
- `src/hypo_agent/channels/qq_bot_channel.py:301-330` `QQBotChannelService.send_message()`
- `src/hypo_agent/gateway/app.py:1184-1210` `refresh_qq_channel_service()`

### 直接原因

`qq_text_renderer.py` 本体没有坏，坏的是“谁在用它”。

1. `render_qq_plaintext()` 本身仍然正确工作，测试与最小化验证都能证明：

```text
• item
【bold】
```

2. 旧 QQ 路径里，它仍通过 `QQAdapter.format()` 被调用。
3. 但当前主 QQ 路径已经变成 `QQBotChannelService.send_message()`。
4. `QQBotChannelService` 维护了一套自己的 `_downgrade_markdown()`，形成了第二套 QQ 文本处理路径。
5. 这导致现在的 QQ 文本处理有两套实现：
   - `QQAdapter.format()` 里的真实混排链路
   - `QQBotChannelService._downgrade_markdown()` 的降级文本链路
6. 只要系统实际走的是 QQ Bot 路径，历史上在 `QQAdapter.format()` 里修好的行为就不会完整生效。

### 额外问题

当前测试覆盖存在空洞：

- `tests/core/test_qq_text_renderer.py` 只测试 renderer 本体
- `tests/gateway/test_qqbot_channel.py` 没有覆盖“列表/标题/粗体/来源标签在 QQ Bot 路径下是否正确”

所以“renderer 单测通过，但线上 QQ 文本体验退化”是完全可能的。

### 回归引入时间

- 历史正确链路来自 `63cfad7`（2026-03-20，M13）
- 当前失效来自**QQ Bot 新路径接管了主通道**，这一部分主要存在于当前工作区未提交文件与 `app.py` 未提交改动中

### 修复方向

收敛成一条 QQ 文本/富文本输出实现；不要同时维护 `QQAdapter.format()` 和 `QQBotChannelService._downgrade_markdown()` 两条规则链。

## D. 跨渠道同步混乱，来源标签丢失或错误，消息可能重复或丢失

### 定位

- `src/hypo_agent/gateway/app.py:1014-1018` `should_sync_webui_session_to_external()`
- `src/hypo_agent/gateway/app.py:1054-1084` `mirror_webui_message_to_qq()`
- `src/hypo_agent/gateway/app.py:1398-1424` `on_proactive_message()`
- `src/hypo_agent/core/pipeline.py:1212-1243` `_broadcast_message()`
- `src/hypo_agent/channels/weixin/weixin_adapter.py:333-346` `_prepend_source_prefix()`
- `src/hypo_agent/channels/qq_channel.py:236-247` `_prefixed_message()`
- `src/hypo_agent/channels/qq_bot_channel.py:287-330` `send_message()`
- `src/hypo_agent/channels/qq_bot_channel.py:237-253` `handle_event()`
- `src/hypo_agent/channels/weixin/weixin_channel.py:200-229` `_handle_message()`
- `src/hypo_agent/gateway/ws.py:168-179` `websocket_chat()`

### 直接原因

#### 原因 1：来源标签不是统一策略，而是散落在三处半

- QQ 旧通道：只给微信来源补 `[微信] `，见 `qq_channel.py:240-247`
- 微信通道：给 QQ / WebUI 补 `[QQ] ` / `[WebUI] `，见 `weixin_adapter.py:339-346`
- WebUI → QQ：靠 `mirror_webui_message_to_qq()` 手动拼 `[WebUI] User:` / `[WebUI] Assistant:`，见 `app.py:1071-1084`
- QQ Bot：没有对应统一前缀注入，见 `qq_bot_channel.py:287-330`

结果：

- “同一条跨渠道消息”的来源标签，取决于目标渠道和走的是哪套 QQ 后端
- QQ Bot 会直接丢掉来源标签

#### 原因 2：广播路径分叉

当前并不存在一个单一的“跨渠道同步总线”。

实际是：

- pipeline 回复：`pipeline._broadcast_message()` → `app.on_proactive_message()` → `channel_dispatcher.broadcast()`
- WebUI 入站消息：`gateway/ws.py` 先直接广播给其他 WebUI 客户端，再单独 `mirror_webui_message_to_qq()`
- WebUI → QQ 不是 dispatcher 的正常分发，而是 `mirror_webui_message_to_qq()` 旁路

所以现在有两套同步机制同时存在：

- dispatcher
- mirror 特判

#### 原因 3：会话同步策略不对称

- WebUI 只有 `session_id == "main"` 才外发，见 `app.py:1014-1018`
- QQ / 微信入站消息都被硬编码到 `session_id="main"`
  - QQ Bot：`qq_bot_channel.py:240`
  - 微信：`weixin_channel.py:203`

这意味着：

- WebUI debug/session 不会同步到外部
- QQ / 微信所有入站默认都会同步出去

这是明显的策略不对称。

### 关于“来源标签在哪里加、在哪里解析”

当前系统里只有“加标签”，没有统一的“解析/归一化来源标签”逻辑。  
来源真正可靠的字段其实是 `Message.channel`，但文本标签又被散落到多处手工拼接，导致：

- `channel` 和文本文案可能不一致
- 某些通道有标签，某些通道没有

### 回归引入时间

- 广播分叉的基础来自 `45c1aa2`（2026-03-10，`M9-S2: QQ channel integration (OneBot11 + dispatcher)`）
- `70c73bc`（2026-03-24，`M14-Fix: preserve channel context and proactive routing across QQ/Weixin/WebUI`）进一步加重了 `on_proactive_message()` / prefix / target_channels 规则分散
- 当前 QQ Bot 路径和 dashboard 对应适配是当前工作区未提交状态，继续把同步策略分叉了一层

### 修复方向

把“路由决策、去重、会话策略、来源标签”统一收敛到一个 dispatcher/policy 层，禁止 `app.py` 里的 QQ 特判旁路。

## E. Dashboard 渠道卡片显示混乱

### 定位

- `src/hypo_agent/gateway/dashboard_api.py:193-235` `channels_status()` 中 QQ/QQ Bot 状态拼装
- `src/hypo_agent/gateway/dashboard_api.py:298-307` 返回结构
- `web/src/views/DashboardView.vue:145-153` `ChannelsStatusResponse`
- `web/src/views/DashboardView.vue:333-353` `channelStatusLabel()`
- `web/src/views/DashboardView.vue:318-331` `channelTagType()`
- `web/src/views/DashboardView.vue:399-417` `channelCardMap.qq`
- `src/hypo_agent/channels/weixin/weixin_channel.py:287-306` `get_status()`

### 直接原因

#### 原因 1：后端把两套 QQ 后端状态揉成一个 `qq` 卡片

`dashboard_api.py` 现在的返回同时包含：

- `channels.qq`
- `channels.qq_bot`
- 顶层 `qq_bot`

而且在 QQ Bot 启用时，会把 `qq_bot_status` 合并进 `qq_status`：

- `dashboard_api.py:195-235`

这使得一个 `qq` 卡片承载了两种不兼容的数据模式。

#### 原因 2：前端类型与后端返回结构不一致

`DashboardView.vue` 的 `ChannelsStatusResponse` 只声明了：

- `webui`
- `qq`
- `weixin`
- `email`
- `heartbeat`

没有声明 `qq_bot`，但后端实际返回了它。

前端又在 `channelCardMap.qq` 里通过 `payload.qq.qq_bot_enabled` 去猜当前 `qq` 卡片到底是旧 QQ 还是 QQ Bot：

- `DashboardView.vue:399-417`

这属于“混合 schema + 运行时猜测”。

#### 原因 3：状态枚举不完整

微信 `get_status()` 可能返回：

- `disabled`
- `error`
- `no_token`
- `connected`
- `disconnected`

但前端 `channelStatusLabel()` 没有处理 `"no_token"`：

- `DashboardView.vue:333-353`

结果是 UI 会直接显示内部状态字串，而不是用户可读状态。

#### 原因 4：`enabled` 被标成绿色成功态

- `DashboardView.vue:321-323`

只要状态是 `"enabled"`，前端就给绿色 tag。  
对 QQ Bot 来说，这通常只代表“配置存在”，并不等于“WebSocket 已连接”。

### 回归引入时间

- 已提交部分：
  - `0d8da6d`（2026-03-24，`M14.1: add recent logs and channel status improvements for dashboard`）
  - `4533292`（2026-03-24，`UX2-R2: dashboard, chat, config, and theme refinements`）
- 直接导致 QQ Bot 卡片混乱的拼装逻辑，目前主要在**当前工作区未提交改动**里

### 修复方向

把“QQ 旧后端”和“QQ Bot 后端”拆成稳定 schema，不要再用一个 `qq` 卡片同时兼容两套字段。

---

## 第三部分：架构问题评估

### 1. 职责划分

当前职责划分不清晰，重叠明显。

#### `app.py`

承担了过多职责：

- 渠道启停与注册
- 会话外发策略
- WebUI 非 main session 过滤
- WebUI → QQ mirror 特判
- 来源标签拼接的一部分
- 渠道状态聚合的一部分前置条件

它已经不是“组装应用”，而是在做“渠道路由控制器”。

#### `pipeline.py`

除了 LLM pipeline 之外，还承担了：

- 广播入口 `_broadcast_message()`
- 事件队列消费
- proactive event → `Message` 映射
- `target_channels` 元数据解析

这使得 pipeline 同时知道“AI 输出”和“渠道广播语义”，耦合过深。

#### `channel_adapter.py`

名义上是通道适配器抽象，但实际上只约束了 `format()`，主要服务于 WebUI event formatting。  
QQ / 微信所谓 “adapter” 根本不共用这一抽象：

- `WebUIAdapter` 是 event formatter
- `QQAdapter` 是 QQ 富文本 segment formatter + sender helper
- `WeixinAdapter` 是 transport sink

名字相同，职责完全不同。

#### `platform_message_preparation.py`

语义上应该是“平台能力裁剪层”，但当前已经把微信规则泄漏到了 QQ，职责边界失守。

#### `channel_dispatcher.py`

它只是最薄的一层 fanout，没有掌握真正的策略。  
真正的排除逻辑、来源标签、main session 限制都在 `app.py` / `pipeline.py` 里。

### 结论

- 没看到严格的循环 import 问题
- 但有严重的职责重叠和“同名不同义”
- 真正的业务策略分散在 `app.py`、`pipeline.py`、各 channel service 里

### 2. 平台隔离性

平台隔离性较差。

典型泄漏：

- `WeixinAdapter` 直接实例化 `QQAdapter` 作为 `_formatter`，见 `weixin_adapter.py:51-55`
- `platform_message_preparation()` 同时处理 `weixin/qq/qq_bot`，见 `platform_message_preparation.py:21-23`
- QQ 旧通道、QQ Bot、微信各自维护不同来源标签逻辑

这说明当前不是“每个平台独立实现自身输出能力”，而是“借别的平台 formatter 再局部打补丁”。

### 3. 可扩展性

如果新增第四个渠道，比如飞书，当前改动面会很大。

至少要改：

- `src/hypo_agent/gateway/app.py`
- `src/hypo_agent/core/channel_dispatcher.py`
- `src/hypo_agent/core/pipeline.py` 的 `_resolve_target_channels()`
- 可能还要改 `platform_message_preparation.py`
- `src/hypo_agent/gateway/dashboard_api.py`
- `web/src/views/DashboardView.vue`
- 如果要来源标签一致，还要碰 QQ/微信现有 prefix 逻辑

改动量不小，而且很容易继续复制：

- 一个新 channel service
- 一个新 adapter
- 一套新 prefix 逻辑
- 一套新 dashboard schema
- 一套新 `target_channels` 白名单

这不是“插件式接入”，而是“侵入式接入”。

### 4. async 一致性

形式上不统一，代码里只能靠防御式 `await`。

- `ChannelAdapter.format()` 协议是 async
- `WebUIAdapter.format()` 是 async
- `QQAdapter.format()` 也是 async
- 但微信/QQ 实际 transport 并不实现这个协议
- `pipeline._format_event()` 只能用 `inspect.isawaitable()` 做兜底，见 `pipeline.py:1299-1313`
- `ChannelDispatcher.broadcast()` 也同时兼容 sync/async sink，见 `channel_dispatcher.py:39-50`

这不会立刻炸，但说明接口抽象不稳定。

### 5. 状态管理：ImageRenderer 生命周期

当前生命周期管理是“启动时初始化，退出时关闭”：

- 初始化：`app.py:755-768`
- 关闭：`app.py:909-913`

`ImageRenderer` 自身能力：

- `initialize()`：创建 playwright/browser/context，见 `image_renderer.py:56-80`
- `shutdown()`：销毁资源，见 `image_renderer.py:151-166`
- `render_to_image()`：每次渲染都依赖已有 context，见 `image_renderer.py:82-101`

问题：

- 没有渲染失败后的自动重建
- 没有 browser/context 死亡后的自愈
- 调用方只能在 renderer unavailable 时退回纯文本
- 也没有独立健康检查或 restart policy

所以它更像“脆弱的进程内单例”，不是“可恢复的渲染服务”。

---

## 第四部分：重构建议（概要）

### 1. 先定义统一的“中间消息模型”，再做平台渲染

Pipeline 不应直接面向 QQ/微信差异，而应先产出统一的中间表示，例如：

- `text_block`
- `code_block`
- `table_block`
- `math_block`
- `diagram_block`
- `image_attachment`
- `provenance`（来源渠道、来源用户、消息 id）

然后各平台 renderer 按能力做映射。

### 2. 按“平台能力等级”分层，而不是按现有文件历史叠补丁

建议至少拆成三类能力：

- Segment-capable：QQ 这类可混排文本/图片段
- Batch-capable：微信这类“文本 + 独立图片批次”
- Rich-card-capable：飞书这类富文本卡片

飞书不应复用 QQ 或微信的 formatter；它应该实现自己的 `render(message, capability)`。

### 3. 把跨渠道同步策略收口到一个 relay policy

统一负责：

- 是否跨渠道外发
- 是否排除原渠道
- 是否排除原 WebUI client
- 来源标签如何生成
- 去重 key 如何生成
- `main` / 非 `main` session 的外发策略

不要再让 `app.py` 里存在 `mirror_webui_message_to_qq()` 这种旁路。

### 4. 把 QQ 富文本实现收敛成一个实现，不要双轨并存

当前最危险的是：

- `QQAdapter.format()` 一套
- `QQBotChannelService._downgrade_markdown()` 又一套

应该保留一个“QQ 渲染器”，然后让不同 QQ transport 只负责：

- NapCat/OneBot：把渲染结果转成 CQ segments
- QQ Bot 官方 API：把渲染结果映射到官方支持的文本/媒体消息

transport 只发，不再自己决定 markdown 怎么降级。

### 5. 把 renderer 做成稳定的本地服务能力

表格/代码/公式/图表渲染建议：

- 不依赖运行时 CDN 成功加载
- 有明确的 health check
- 失败后可自动 reinitialize
- 有统一 fallback 规则

这样未来接入飞书时，既可以把图片插入卡片，也可以在支持时直接输出富文本块。

---

## 附：本次审查的核心判断

当前渠道系统的主要问题不是“某一处小 bug”，而是：

1. QQ 已经出现“旧链路”和“新链路”并存
2. 微信实现复用了 QQ formatter，平台边界已经泄漏
3. 广播/来源标签/会话外发策略没有单一真相源
4. Dashboard 也在同时兼容两套 QQ 后端，导致 schema 混乱

从重构优先级看，建议先做：

1. 统一跨渠道 relay policy
2. 统一中间消息模型
3. 收敛 QQ 渲染实现
4. 再接入飞书

