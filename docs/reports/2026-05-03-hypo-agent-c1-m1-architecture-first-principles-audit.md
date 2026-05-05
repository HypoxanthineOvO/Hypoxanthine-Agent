# C1-M1 总体架构审判报告

> 时间：2026-05-03 01:36 Asia/Shanghai  
> Milestone：M1: 总体架构审判  
> 结论：审计通过，但发现系统级契约缺口；后续 M2-M8 仍然必要，且应优先修复资源引用、工具恢复和网页读取的统一抽象。

## 摘要

本 Milestone 没有做大范围重构，只新增了一个只读架构契约探针：

- `scripts/audit_system_contracts.py`
- `tests/scripts/test_audit_system_contracts.py`

探针扫描了 5 个关键维度：资源引用、工具恢复、网页读取、渠道文件发送、前端可观测性。当前结果为 11 个发现，其中 6 个 Critical、5 个 Warning。第一性原理判断是：Hypo-Agent 已经有不少局部能力，但这些能力没有被统一成“可定位、可恢复、可解释、可确认”的系统契约。

## 全局故障路径

### 文件定位路径

```text
用户说“读某个文件”
  -> Message.text / Attachment(url, filename)
  -> Pipeline 让模型生成 read_file(path)
  -> SkillManager.invoke(raw params)
  -> FileSystemSkill._resolve_read_target()
  -> 命中则读取；未命中则 File not found
  -> ToolOutcome 将 not found/required/missing 归为 user_input_error, retryable=False
  -> WebUI 只能显示工具错误或普通 retry，不能展示候选确认
```

证据：

- `src/hypo_agent/models.py:11` 定义 `Attachment(url, filename, mime_type)`，`Message.file/image/audio` 仍是 deprecated loose fields；没有 `ResourceRef`。
- `src/hypo_agent/skills/fs_skill.py:182` 的 `read_file` 缺路径时报 `path is required`，找不到时报 `File not found`。
- `src/hypo_agent/skills/fs_skill.py:501` 只枚举固定候选路径并返回第一个存在项或第一个猜测项，没有 ranked candidates。
- `src/hypo_agent/core/tool_outcome.py:97` 将 not found / required / missing 全部归入不可重试的 `user_input_error`。

### URL/网页阅读路径

```text
用户输入 URL
  -> 模型调用 web_read(url)
  -> AgentSearchSkill 先加载 Tavily API key
  -> Tavily client.extract(markdown)
  -> 如果是知乎且抽取失败，进入手写 Zhihu API fallback
  -> 仍失败则返回工具错误
```

证据：

- `src/hypo_agent/skills/agent_search_skill.py:166` 的 `web_read` 先调用 Tavily `client.extract`。
- `src/hypo_agent/skills/agent_search_skill.py:189` 只对知乎 URL 进入 API fallback。
- `src/hypo_agent/skills/agent_search_skill.py:233` 在 fallback 策略之前就要求 Tavily key 存在。
- 项目已有 Playwright 依赖和 auth/runtime 测试，但网页读取主路径没有 browser-rendered reader。

### 工具调用和恢复路径

```text
模型生成 tool_call
  -> Pipeline parse arguments
  -> SkillManager.invoke(params)
  -> SkillOutput(status/result/error_info)
  -> classify_tool_outcome()
  -> Pipeline 根据 _RETRYABLE_TOOLS 或 outcome 元数据决定是否再试
  -> WebSocket 发 tool_call_start/result/error
```

问题在于恢复不是一个显式状态机。系统知道“超时可重试”，也有局部 retryable 工具表，但不知道“缺参数 -> 搜索上下文 -> 提供候选 -> 询问用户 -> 继续同一操作”这个完整闭环。

证据：

- `src/hypo_agent/core/pipeline.py` 存在 `_RETRYABLE_TOOLS` 和大量工具事件处理逻辑。
- `src/hypo_agent/core/skill_manager.py` 接收 `params: dict[str, Any]`，但没有集中式 JSON Schema 参数校验和恢复建议输出。
- `src/hypo_agent/core/tool_outcome.py:104` 对缺参/缺资源给出不可重试结果。

### 渠道发文件路径

```text
Assistant/SkillOutput 带 attachments
  -> ChannelRelayPolicy 选择渠道
  -> Feishu/QQ Bot/Weixin 各自尝试上传或构造文件消息
  -> DeliveryResult 只汇总 success/error/segment_count
  -> WebUI/调用方看不到每个渠道的附件能力、降级方式、确认动作
```

证据：

- `src/hypo_agent/channels/feishu_channel.py` 有 `upload_file` 和 `_upload_file_bytes`。
- `src/hypo_agent/channels/qq_bot_channel.py` 有 `_send_file_with_fallback`。
- `src/hypo_agent/channels/weixin/weixin_adapter.py` 有 `_build_file_item`。
- `src/hypo_agent/core/delivery.py:9` 的 `DeliveryResult` 只描述 channel、success、segment_count、failed_segments、error、timestamp。
- `src/hypo_agent/core/channel_dispatcher.py:205` 对非 main session 的外部渠道静默跳过，最多进入 debug trace，不是用户可见的 capability decision。

### 前后端状态同步路径

```text
Pipeline emits tool_call_start/result/error
  -> WebSocket/composable 写入消息列表
  -> MessageRenderer / ToolCallMessage / ErrorStateCard 展示状态
  -> 用户最多点击 message-level retry
```

证据：

- `web/src/types/message.ts:17` 只把 `MessageEventType` 定义为 `tool_call_start | tool_call_result`。
- `web/src/types/message.ts:163` 有 `ToolCallErrorEvent`，但没有 resource candidates、disambiguation、confirmation event。
- `web/src/components/chat/ErrorStateCard.vue:45` 的 retry 是错误卡片级别，不绑定后端结构化 recovery action。

## 第一性原理问题清单

| 优先级 | 问题 | 判断 |
| --- | --- | --- |
| Critical | 资源不是第一类对象 | 文件、附件、URL、网页、导出物都被松散字段表达，系统无法稳定解析“那个文件/这个链接/刚才的附件”。 |
| Critical | 缺资源被当成不可恢复用户错误 | not found 和 missing 应该触发搜索、候选、追问或 fallback，而不是直接终止工具循环。 |
| Critical | 网页读取没有 provider-agnostic reader | Tavily + 知乎 API fallback 不能覆盖动态网页、登录态、反爬、需要渲染的页面。 |
| Critical | 渠道文件能力没有统一 contract | 三个渠道有实现碎片，但没有告诉上层“能不能发、怎么发、失败如何降级”。 |
| Critical | 前端缺少候选确认协议 | WebUI 能显示工具状态，但不能承载“我找到了 3 个候选文件，请确认”。 |
| Warning | 工具参数缺少集中校验 | raw params 直接进 skill，缺失字段不能产生统一 repair hint。 |
| Warning | 主动恢复不是状态机 | retry、fallback、ask、verify 分散在 prompt 和局部逻辑里，不是可观测流程。 |
| Warning | 测试多为局部证明 | 单元/集成测试证明组件行为，不证明真实 URL、真实渠道文件发送和端到端恢复。 |

## 测试可信度

可信：

- `tests/scripts/test_audit_system_contracts.py` 能稳定证明当前代码中存在 5 个维度的契约缺口。
- 后端文件、pipeline、tool、channel adapter、WebUI 消息组件测试能证明局部接口没有明显回归。
- 现有 Feishu/QQ/Weixin 附件测试能证明各渠道存在部分文件处理原语。

只能证明局部：

- `read_file` 测试证明可读取已知路径和部分格式，但不证明模糊文件引用、候选搜索、用户确认。
- `web_read` 测试证明 Tavily mock 和知乎 API fallback，但不证明浏览器渲染 fallback、登录态页面、cookie/session 续用。
- WebUI tool event 测试证明开始/结果/错误渲染，但不证明资源候选确认和操作级恢复。

仍缺失的真实场景：

- 用户只说文件名、别名或“刚才那个文件”时的端到端定位。
- 知乎等真实 URL 的动态渲染和登录态读取。
- QQ/微信/飞书真实文件 outbound 验收和降级提示。
- 工具失败后自动搜索、追问、重试、验证的完整 trace。
- 前端候选确认后恢复同一个 backend operation 的契约测试。

## 探针结果

命令：

```bash
uv run python scripts/audit_system_contracts.py --json
uv run pytest tests/scripts/test_audit_system_contracts.py -q
```

结果：

- 探针：5 dimensions, 11 findings, 6 critical, 5 warning。
- 探针测试：2 passed。
- 伴随 warning：第三方 `lark_oapi` / `websockets` deprecation warnings，不影响 M1 判断。

## 对 M2-M8 的调整建议

M2 资源引用统一模型应作为最优先基础，不只是新增 `ResourceRef` 类型，还要包含：

- `ResourceRef`：统一表达 file、attachment、url、webpage、generated artifact、channel media。
- `ResourceResolver`：从文本、附件、会话历史、上传目录、导出目录中解析候选。
- `ResourceCandidate` / `ResourceConfirmation`：把模糊引用变成可确认的结构化事件。

M3 工具调用契约与恢复循环应依赖 M2：

- 工具调用前做 schema 校验。
- `ToolOutcome` 增加 `recovery_action`、`candidates`、`missing_fields`、`resume_token`。
- Pipeline 引入显式 `search -> ask -> retry -> fallback -> verify` 状态机。

M4 网页阅读架构应重做为 provider-agnostic：

- HTTP reader、extract provider、browser reader、site-specific provider 分层。
- Tavily 不应是读取网页的前置硬依赖。
- Playwright/browser session 应成为正式 fallback，而不是只存在 auth 相关能力里。

M5 渠道文件能力应把已有实现收束成 contract：

- `ChannelCapability` 声明附件类型、大小、上传方式、fallback。
- `DeliveryResult` 携带 per-attachment outcome。
- Relay policy 对 session/channel 跳过要变成用户可见的交付决策。

M6-M7 应围绕同一恢复事件模型推进：

- 后端 operation trace 要能被 WebUI 显示。
- WebUI 要支持候选确认、继续同一操作、展示 fallback path。
- message-level retry 需要升级为 operation-level recovery action。

M8 验收必须包含真实场景矩阵，不能只跑 mock 单测：

- local fixture + contract tests。
- mocked provider integration。
- Playwright browser smoke。
- real/sandbox channel file delivery gate。

## 评估

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| diff_score | 1/5 | 只新增只读探针、探针测试和报告，没有改变运行时行为。 |
| code_quality | 2/5 | 探针结构简单、可读，但属于审计脚本，不是生产抽象。 |
| test_coverage | 3/5 | 覆盖 M1 证据需求，但真实场景验收仍缺失。 |
| complexity | 2/5 | 新增复杂度低，报告指出的后续改动复杂度高。 |
| architecture_drift | 1/5 | 没有引入新架构路径，只记录现状和后续方向。 |
| overall | 2/5 | M1 作为审计 Milestone 可通过；系统级风险需在 M2-M8 解决。 |

判定：PASS with Critical Findings。  
下一步：进入 M2，先建立资源引用统一模型和候选确认契约。
