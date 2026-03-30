# Channel System Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重构渠道系统，引入统一消息模型、统一 relay 策略层，并让 QQ / 微信渲染器与 transport 清晰解耦，为后续接入飞书预留稳定扩展点。

**Architecture:** 保留现有 `Message` 作为入站与会话存储模型，但新增 `UnifiedMessage` 作为跨渠道广播与平台渲染的统一载体。`ChatPipeline` 与各渠道入站适配器统一把 `Message` 转成 `UnifiedMessage` 交给 `ChannelRelayPolicy`，再由平台 renderer 输出有序 segment，最后由各 transport 翻译成具体协议。

**Tech Stack:** Python / FastAPI / Pydantic / Playwright / Vue 3 / Naive UI / pytest

---

### Task 1: 引入统一消息模型与转换器

**Files:**
- Create: `src/hypo_agent/core/unified_message.py`
- Modify: `src/hypo_agent/models.py`
- Test: `tests/core/test_relay_policy.py`

**Steps:**
1. 定义 `UnifiedMessage`、内容块、provenance、消息类型与辅助工厂。
2. 保留现有 `Message`，避免入站协议和会话存储接口回归。
3. 提供 `Message -> UnifiedMessage` 转换，支持 `text/code/table/math/diagram/image_attachment/file_attachment`。

### Task 2: 修复 MarkdownSplitter 的 GFM 表格识别

**Files:**
- Modify: `src/hypo_agent/core/markdown_splitter.py`
- Test: `tests/core/test_markdown_splitter.py`

**Steps:**
1. 放宽表格识别到标准 GFM 变体。
2. 保证 fenced code 仍优先于表格识别。
3. 补标准/无首尾 `|`/带前后文本等测试。

### Task 3: 强化 ImageRenderer 和本地 marked bundle

**Files:**
- Modify: `src/hypo_agent/core/image_renderer.py`
- Modify: `src/hypo_agent/templates/render.html`
- Create: `src/hypo_agent/templates/vendor/marked.min.js`
- Test: `tests/core/test_image_renderer.py`

**Steps:**
1. 增加 `health_check()`。
2. 对 page/context/browser 崩溃场景做一次重建后重试。
3. 定义文本 fallback，渲染失败不丢内容。
4. 把 `marked` 切到本地 bundle。

### Task 4: 收敛 QQ 渲染器与两类 QQ transport

**Files:**
- Create: `src/hypo_agent/core/qq_renderer.py`
- Modify: `src/hypo_agent/channels/qq_adapter.py`
- Modify: `src/hypo_agent/channels/qq_bot_channel.py`
- Modify: `src/hypo_agent/channels/qq_channel.py`
- Test: `tests/core/test_qq_renderer.py`
- Test: `tests/core/test_channel_adapter_qq.py`
- Test: `tests/gateway/test_qqbot_channel.py`

**Steps:**
1. 新增 `QQRenderer` 统一输出 ordered segment。
2. NapCat / QQ Bot 都复用 `QQRenderer`。
3. 删除 QQ Bot 私有 markdown 降级逻辑。
4. 保持现有 HTTP / WS / webhook / fallback 行为不退化。

### Task 5: 重写 WeixinRenderer，切断对 QQAdapter 的依赖

**Files:**
- Create: `src/hypo_agent/core/weixin_renderer.py`
- Modify: `src/hypo_agent/channels/weixin/weixin_adapter.py`
- Modify: `src/hypo_agent/core/platform_message_preparation.py`
- Test: `tests/core/test_weixin_renderer.py`
- Test: `tests/channels/test_weixin_adapter.py`

**Steps:**
1. 微信独立渲染文本与图片批次。
2. `platform_message_preparation.py` 仅保留微信相关预拆分。
3. 微信 transport 继续负责 context token、图片上传与降级。

### Task 6: 实现 ChannelRelayPolicy 并接管广播

**Files:**
- Modify: `src/hypo_agent/core/channel_dispatcher.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/channels/weixin/weixin_channel.py`
- Modify: `src/hypo_agent/channels/qq_channel.py`
- Modify: `src/hypo_agent/channels/qq_bot_channel.py`
- Test: `tests/core/test_relay_policy.py`
- Test: `tests/gateway/test_webui_qq_sync.py`

**Steps:**
1. 实现会话过滤、来源标签注入、原渠道排除、原 WebUI 客户端排除、去重。
2. 删除 `mirror_webui_message_to_qq()` 和各处 prefix 拼接。
3. 让 pipeline 只调用 relay policy，不再内嵌频道判断。

### Task 7: 整理 Dashboard schema

**Files:**
- Modify: `src/hypo_agent/gateway/dashboard_api.py`
- Modify: `web/src/views/DashboardView.vue`
- Modify: `web/src/components/dashboard/ChannelStatusCard.vue`
- Test: `tests/gateway/test_channels_status_api.py`

**Steps:**
1. `channels.qq_bot` / `channels.qq_napcat` 独立返回。
2. 前端类型与后端对齐。
3. 补充状态文案覆盖 `no_token` / `disabled` 等值。

### Task 8: 验证与回归测试

**Files:**
- Test: `tests/core/test_markdown_splitter.py`
- Test: `tests/core/test_image_renderer.py`
- Test: `tests/core/test_qq_renderer.py`
- Test: `tests/core/test_weixin_renderer.py`
- Test: `tests/core/test_relay_policy.py`
- Test: `tests/channels/test_weixin_adapter.py`
- Test: `tests/core/test_channel_adapter_qq.py`
- Test: `tests/gateway/test_webui_qq_sync.py`
- Test: `tests/gateway/test_qqbot_channel.py`
- Test: `tests/gateway/test_channels_status_api.py`

**Steps:**
1. 先跑核心单测，再跑 gateway/channel 集成测试。
2. 确认测试模式下仍不注册真实 QQ transport。
3. 最后补一次更大范围 pytest smoke。
