# C4 M1 最近一个月失败审查与真实案例回放

审查窗口：2026-04-05 到 2026-05-05  
产出时间：2026-05-05  
范围：工具调用、模型调用、文件/附件、QQ 图文入站、图片生成与渠道投递、Codex/Agent repair 作业。

## 结论摘要

最近一个月 `memory/hypo.db.tool_invocations` 内共有 1671 次工具调用，其中 `success=1533`，`error=99`，`fused=22`，`blocked=17`。失败不是单点问题，而是同一类可恢复错误在多层被重复呈现：模型先构造错误参数，工具返回 schema/file/API 错误，breaker 把连续错误升级成 disabled，WebUI/渠道又把中间错误直接外显，最终摘要反而缺失。

应优先修的是体验协议，而不是简单放宽失败次数。可恢复失败应只更新同一个“正在调用 <工具展示名>”状态；只有模型链路和工具恢复动作全部耗尽时，才展示最终失败摘要。否则继续加失败次数会让 Notion schema、文件路径猜测、命令 allowlist 这类错误失败更多次，用户看到的噪声也更多。

最严重的两个静默问题是：QQBot 入站图文只读取 `content`，没有把图片转换成 `Message.attachments`；模型/流水线有 fallback 机制，但外层把多类异常压成 `LLM 调用失败，请检查配置或稍后重试`，用户看不到主模型、备用模型、失败原因和是否会自动恢复。

## 数据源与方法

查询了 `memory/hypo.db` 的 `tool_invocations`、`token_usage`、`repair_runs`、`repair_run_events`，检查了 `memory/sessions/main.jsonl`、`logs/backend.log`、`.pipeline/debug/*.md`、`docs/reports/*.md`，并对照了关键代码路径。

数据库时间存在混合格式：`tool_invocations.created_at` 多为无时区字符串，`token_usage` 和 `repair_runs` 多为 UTC ISO 字符串。本报告按 `[2026-04-05, 2026-05-06)` 查询，避免遗漏 2026-05-05 当天记录。`memory/agent.db` 和根目录 `hypo.db` 未发现有用业务表；`codex_jobs` / `codex_job_events` 当前为空，因此 repair 作业以 `repair_runs` 为准。

主要观测缺口：1063 条最近工具记录没有 `outcome_class`；多数失败没有可跨模型调用、工具调用、渠道投递贯穿的 `trace_id`；模型失败没有完整落库，只能从 `token_usage`、日志和会话文本侧面复原。

## 失败总览

| 类别 | 数量/现象 | 代表根因 |
|---|---:|---|
| Notion/schema/API | 42 起左右 | 属性名/类型/排序字段不匹配、filter JSON 无效、接口超时 |
| 文件/路径 | 41 起左右 | 读不存在的导出文件、相对路径猜错、报告路径/文件名拼错 |
| breaker fused/blocked | 28 起 | 可恢复错误连续 3 次后被禁用，放大后续失败 |
| 命令执行 | 15 起 | allowlist miss、shell 控制符禁用、命令不存在、系统权限要求 |
| 图片生成 | 2 起 | CLI fallback 需要 API key 或路径选择错误 |
| Web read | 3 起 | Zhihu pin 无可抽取内容，缺浏览器/结构化 fallback |

按工具聚合的失败前几位：`read_file=62`，`notion_query_db=27`，`exec_command=21`，`list_directory=4`，`notion_create_entry=4`，`notion_update_page=4`，`get_notion_todo_snapshot=3`，`web_read=3`。

失败高峰出现在 2026-04-25 到 2026-04-26：这两天集中出现 Notion 导出、read_file 查找导出文件、exec_command cat 文件、web_read Zhihu、repair 任务失败/丢失，说明“任务链路恢复”比单工具成功率更值得优先修。

## 真实案例回放

### 案例 A：Notion schema 错误被当成普通工具失败反复尝试

2026-04-19 到 2026-04-25，`notion_query_db` 多次因 `Could not find property with name or id: Name/Status/状态/时间/Due Date`、filter 类型不匹配、body validation 失败而报错。2026-04-21 和 2026-04-22 多次连续失败后触发 `Tool 'notion_query_db' has been disabled after 3 consecutive failures this session`；2026-04-24 还出现 `session circuit breaker is open for 'main'`。

这类错误不应该靠“允许更多失败次数”解决。它需要在工具层返回 schema mismatch 的结构化恢复提示：当前数据库属性、期望属性、可替代属性和是否可自动重写查询。否则模型会继续换参数试错，breaker 会把可修复参数问题升级成工具不可用。

相关代码风险：`channel_progress.py` 对非 success 的 `tool_call_result` 直接返回失败文案（`src/hypo_agent/core/channel_progress.py:115`），WebUI 也把失败项作为工具结果消息推入列表（`web/src/composables/useChatSocket.ts:748`）。因此中间错误容易外显。

### 案例 B：文件/附件导出链路多次读错临时文件并熔断

2026-04-25 到 2026-04-26，`read_file` 连续读取类似 `20260425_110039_tool-output-main-get_heartbeat_snapshot-...md` 的根目录相对路径，实际文件不存在，于是出现大量 `File not found: /home/heyx/Hypo-Agent/...`。同一窗口内 `read_file` 很快进入 fused/blocked，后续 `exec_command cat ...` 也继续失败。

这暴露的是资源引用没有被规范化：工具输出、导出文件、附件文件应使用稳定 `resource_id` 或带来源的 artifact descriptor，而不是让模型根据展示文件名猜路径。`.pipeline/debug/debug-001-notion-export-file-attachments.md` 和 `debug-002-file-attachments-unnamed-duplicates.md` 也记录过文件附件在渠道出口被降级、重复或丢名的问题，说明文件/附件链路需要统一处理，不只是读文件工具本身。

建议 M2/M6 回放该链路：工具输出生成 artifact -> 用户要求“发给我/读一下” -> resolver 定位真实文件 -> 渠道按 attachment 投递 -> 中间 read_file 失败不外显。

### 案例 C：用户只看到 `LLM 调用失败`，但系统没有给出模型链路

`memory/sessions/main.jsonl` 在 2026-04-05 到 2026-04-06 有多条系统心跳回复：`Heartbeat 执行失败：LLM 调用失败，请检查配置或稍后重试`。2026-04-06 之后用户追问“不是有fallback吗 咋又失败了”，说明错误摘要没有解释模型 fallback 状态。

代码上，`ModelRouter.stream()` 确实有 candidate chain，并会发 `model_fallback` / `model_fallback_exhausted` 事件（`src/hypo_agent/core/model_router.py:346`、`src/hypo_agent/core/model_router.py:516`、`src/hypo_agent/core/model_router.py:526`）。但如果流式输出已经开始后失败，会记录 `model_stream_failed_after_output` 然后直接 raise（`src/hypo_agent/core/model_router.py:498`）；流水线外层再把 `RuntimeError` 和普通 `Exception` 统一压成 `LLM_RUNTIME_ERROR`，文案固定为 `LLM 调用失败，请检查配置或稍后重试`（`src/hypo_agent/core/pipeline.py:4040`、`src/hypo_agent/core/pipeline.py:4050`、`src/hypo_agent/core/pipeline.py:4058`）。

这解释了“有时再发一遍就成功”：第一次可能主模型超时或流中断，外层摘要丢失；第二次重新走 candidate chain 成功。M4 应把最终失败事件改成包含 `requested_model`、attempted chain、每个候选失败原因、是否 vision-capable、是否已经 fallback 的结构化摘要。

### 案例 D：QQBot 图文入站是静默数据丢失，不会出现在工具失败表里

当前 QQBot 入站解析只取 `data.content`：`_parse_inbound_event()` 在 `src/hypo_agent/channels/qq_bot_channel.py:622` 到 `src/hypo_agent/channels/qq_bot_channel.py:660` 返回 `QQBotInboundEvent(content=...)`；`handle_event()` 构造 `Message(text=...)` 时没有传入 `attachments`（`src/hypo_agent/channels/qq_bot_channel.py:269`）。所以用户在 QQ 同时发图片和文字时，Agent 只看到文字，看不到图。

这是 silent failure：因为没有工具调用，也没有异常，`tool_invocations` 不会记录。M5 必须补入站媒体解析、下载/引用、`Message.attachments` 填充和测试。已有 NapCat/QQ 出站附件能力和 QQBot 出站图片/文件测试，但入站图文测试缺口明显。

### 案例 E：图片生成走错或暴露了底层 CLI 失败

2026-05-04 `generate_image` 两次失败，错误摘要以 `Image generation failed: OPENAI_API_KEY is set. Calling Image API... Traceback...` 开头。系统 imagegen skill 明确默认应使用内建 `image_gen`，CLI fallback 才依赖 `OPENAI_API_KEY`；但 Hypo-Agent 的 `ImageGenSkill` 仍有 CLI 执行路径和恢复逻辑，失败时被记录为 `tool_bug`。

该类失败对用户应该呈现为“图片生成失败：已尝试内建/CLI 路径，失败点为认证/网络/API”，而不是直接露出 traceback。它也应该不触发会影响后续无关工具的全局 breaker。

### 案例 F：Agent repair 作业自身也会失败或停在 needs_review

最近一个月 `repair_runs` 共 15 条：`completed=5`，`failed=5`，`needs_review=4`，`aborted=1`。失败原因包括 `thread is not materialized yet`、`task.lost_on_restart`、`interrupted`，并集中出现在修复 Notion、模型连接、文件访问错误的时期。

这说明 Codex/Agent 作业失败也需要纳入同一套最终摘要：提交了什么任务、执行器状态、是否丢线程、是否可重试、下一步需要人工还是自动恢复。目前 `codex_jobs` 为空，repair 作业和通用 Agent job 观测没有统一。

## 为什么会失败好几次

第一层是模型参数构造错误：Notion 属性名、filter 类型、文件路径、shell 命令 profile 选择都依赖模型临场猜测。第二层是工具契约不够结构化：工具返回自然语言错误多，缺少 machine-readable recovery hint。第三层是 retry/breaker 粒度太粗：同一 session 内连续 3 次失败就可能禁用工具，但这些失败可能是同一用户任务内的参数修正过程。第四层是呈现层过早外显：WebUI 会把 start/result/error 都作为消息或 progress item 展示，渠道也在最终失败前发送错误。

因此不建议单纯“允许更多次失败”。更好的规则是：可恢复失败不计入用户可见失败；参数修正型失败不应打开 session breaker；只有工具实现 bug、认证/权限、外部服务持续不可用或所有恢复动作耗尽，才进入最终失败摘要。

## 用户可见状态建议

中间态：只保留一个活动项，例如 `正在调用 读取文件`、`正在调用 查询 Notion`、`正在生成图片`。同一个 `tool_call_id` 或同一个 logical operation 的重试更新同一项，不新增失败消息。

最终成功：替换为结果或移除短暂成功状态；如果需要回放，展示“已完成：读取文件，重试 2 次后成功”。

最终失败：展示摘要，包括工具展示名、失败类别、尝试次数、关键错误、是否 fallback、下一步建议。示例：`查询 Notion 失败：已尝试 3 次，数据库没有 Status/状态 字段；已读取 schema，无法自动匹配。`

纯对话：不显示中间态，除非超过阈值或进入模型 fallback。图片/文件/工具调用/长任务可以显示处理中。

## 映射与呈现缺口

工具展示名目前分散在三处：`src/hypo_agent/core/tool_narration.py` 的 `_TOOL_LABEL_OVERRIDES`，`config/narration.yaml` 的模板，和 `src/hypo_agent/core/channel_progress.py` 的 `_TOOL_STATUS_TEMPLATES`。WebUI 仍直接显示 `payload.tool_name`（`web/src/composables/useChatSocket.ts:502`、`web/src/composables/useChatSocket.ts:525`）。M3 应统一一个工具展示/状态 registry，WebUI 与渠道共用同一套 label、verb、category、retriable 文案。

## 模拟测试清单

1. Notion schema mismatch：第一次 `Status` 不存在，工具返回 schema hint，模型自动改用 `状态` 或停在最终摘要；中间不显示失败。
2. 文件 artifact resolver：模型请求读取展示文件名，resolver 找到真实导出路径；若找不到，最终摘要列候选路径和来源，不触发 read_file breaker。
3. 工具参数错误重试：`exec_command` allowlist miss 后改用允许命令；WebUI 只保留一个“正在执行命令”，成功后回放“重试 1 次后成功”。
4. LLM 主模型超时：primary timeout 后 fallback 到 vision-capable 模型；用户只收到最终回答，后台记录 chain。
5. LLM 全链路失败：primary、fallback1、fallback2 全失败，最终错误展示 attempted chain、每个失败原因和 retryable 状态，不再只说 `LLM 调用失败`。
6. QQBot 图文入站：构造 `C2C_MESSAGE_CREATE` 含 `content` 与图片附件，pipeline 收到 `Message.text` 和 `attachments[0].type=image`。
7. 图片生成 fallback：内建生成失败后，如允许 CLI fallback，最终摘要说明内建/CLI 各自失败点；不泄露 traceback。
8. 渠道发送重试：QQ/微信发送图片失败降级为文本或文件链接时，只输出一次最终投递结果，不刷 retry 细节。

## M2-M6 修复优先级建议

1. M2 先做工具调用状态折叠与最终摘要。它能立刻缓解“失败刷屏”，也为后续 M3/M4/M5 提供统一事件契约。
2. M4 提前和 M2 联动设计失败摘要 schema。模型 fallback 失败是用户最难理解的体验问题，尤其图片理解任务需要 vision-capable fallback。
3. M5 修 QQBot 入站图文。它是静默数据丢失，优先级应高于单纯补展示名。
4. M3 统一工具展示名映射。它应服务 M2 和渠道消息，不建议继续让 WebUI、channel_progress、narration 各自维护一份。
5. M6 用本报告案例做端到端回放，同时补 `tool_invocations`/repair/model/channel 的 trace 关联验收。

## 验收状态

本 Milestone 未修改业务代码，只新增本审查报告。已覆盖至少 6 个真实失败案例，并给出 8 类模拟测试场景。建议在进入 M2 前，用本报告复审里程碑顺序：M2/M4/M5 可能需要并行设计同一个失败摘要协议，但实现仍可按现有计划串行推进。
