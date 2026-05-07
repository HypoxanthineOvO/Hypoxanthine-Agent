# C5/M1 真实失败回放与事件流审查

## 结论

三类真实样本的根因一致：`ChatPipeline.stream_reply()` 会在 ReAct 中把工具/模型的中间失败事件立即发给渠道 `emit` 回调；QQ、微信、飞书等渠道再通过 `summarize_channel_progress_event()` 把这些事件转换成用户可见文本。该路径没有等待同一轮任务的最终结果，所以出现“先报错，后成功”的割裂体验。

## 事件流

1. 渠道入站消息构造 `emit` 回调并入队。
2. `ChatPipeline._consume_user_message_event()` 将渠道 `emit` 作为 `event_emitter` 传给 `stream_reply()`。
3. `stream_reply()` 在每个工具调用阶段 yield 或 emit：
   - `tool_call_start`
   - `tool_call_error`
   - `tool_call_result`
   - `model_fallback`
   - `assistant_chunk`
   - `assistant_done`
4. QQ/微信/飞书 `emit` 回调调用 `summarize_channel_progress_event()`。
5. 旧逻辑会把非 success 的 `tool_call_result` 和没有 `will_retry=true` 的 `tool_call_error` 直接变成用户可见失败。

## 真实样本归因

### Notion 计划通

- 外显错误：`Notion page not found: HYX的计划通`
- 当前分类：`missing_resource` / `user_input_error`
- 实际性质：可恢复探测失败。后续工具找到了真实计划通位置并完成写入。
- 修复入口：`channel_progress.py` 不应显示非终态工具失败；后续 M2/M4 补强错误分类和 Notion Plan fallback。

### TicNote 搜索

- 外显错误：`模型调用失败：Request timed out after 60 seconds.`
- 当前分类：运行时模型错误。
- 实际性质：timeout 类可恢复错误；模型/搜索后续可重试或 fallback。
- 修复入口：`pipeline.py` 的 RuntimeError timeout-like 分支需要归一为 `LLM_TIMEOUT`，不暴露 provider 原始错误文本；后续 M4 补强 fallback。

### 图片解释

- 外显错误：`读取文件 失败：File not found: /home/heyx/Hypo-Agent/weixin-...-image.png`
- 当前分类：`missing_resource`
- 实际性质：可恢复路径 miss；后续附件/upload fallback 或视觉模型仍能成功解释。
- 修复入口：`channel_progress.py` 隐藏中间 `read_file` 失败；后续 M4 保证附件路径 fallback。

## 已加红测

- `tests/core/test_channel_progress.py::test_channel_progress_suppresses_intermediate_tool_result_failure`
- `tests/core/test_channel_progress.py::test_channel_progress_suppresses_recoverable_tool_error_without_retry_flag`
- `tests/core/test_channel_progress.py::test_channel_progress_suppresses_successful_model_fallback_notice`
- `tests/unit/test_pipeline_error_handling.py::test_pipeline_timeout_runtime_error_uses_generic_timeout_message`

## M1 最小修复

- `src/hypo_agent/core/channel_progress.py`
  - 成功 fallback 的 `model_fallback` 不再外显。
  - 非终态 `tool_call_result` 失败不再外显。
  - `retryable=true` 且非终态的 `tool_call_error` 不再外显。
  - 只有 `terminal=true` 的工具失败才转换成渠道失败摘要。
- `src/hypo_agent/core/pipeline.py`
  - `RuntimeError("Request timed out...")` 等 timeout-like 错误归一为 `LLM_TIMEOUT`。
  - 用户可见文本改为 `模型调用超时，请稍后重试`，不暴露原始 provider timeout 文本。

## 验证

- `uv run pytest tests/core/test_channel_progress.py tests/unit/test_pipeline_error_handling.py -q`
- 结果：11 passed。

## 后续 M2-M6 边界

- M2：将上述隐式规则整理成统一错误分类策略。
- M3：实现更强的“调用中”聚合，避免同轮多个工具 start 反复刷屏。
- M4：补强模型/搜索/Notion/文件读取 fallback。
- M5：把生产默认运行切到非阻塞消息执行，保证慢任务不阻塞后续消息。
- M6：用真实样本和模拟矩阵做端到端验收。
