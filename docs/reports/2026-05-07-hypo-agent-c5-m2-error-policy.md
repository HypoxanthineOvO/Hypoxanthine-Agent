# C5/M2 错误分类与用户可见策略

## 策略

渠道侧只展示两类内容：

- 稳定进度：例如“正在搜索网页”“正在读取计划通结构”。
- 终态结果：assistant 最终回复，或 `terminal=true` 的最终失败摘要。

以下内容默认不进入用户聊天窗口：

- 非终态 `tool_call_error`。
- 非终态且非 success 的 `tool_call_result`。
- 成功 fallback 过程中的 `model_fallback`。
- provider 原始 timeout 文本，例如 `Request timed out after 60 seconds.`。

## 分类落点

- `recoverable`：同轮仍可能继续的工具/模型失败。当前实现通过非 `terminal=true` 的 progress event 表示，渠道不外显。
- `terminal`：最终失败。当前实现要求 event 携带 `terminal=true`，渠道才使用 `summarize_tool_failure()` 生成摘要。
- `timeout`：模型 RuntimeError 中含 `timeout`/`timed out` 时归一为 `LLM_TIMEOUT`，用户只看到通用中文摘要。

## 本轮改动

- `src/hypo_agent/core/channel_progress.py`
  - `tool_call_error` 必须 `terminal=true` 才外显。
  - `tool_call_result` 非 success 必须 `terminal=true` 才外显。
  - `model_fallback` 成功切换备用模型时不外显。
- `src/hypo_agent/core/pipeline.py`
  - timeout-like RuntimeError 归一到 `LLM_TIMEOUT`。

## 验证

- `uv run pytest tests/core/test_channel_progress.py tests/unit/test_pipeline_error_handling.py tests/core/test_pipeline.py::test_successful_fallback_is_not_visible_on_external_channel -q`
- 结果：14 passed。

## 影响

- Notion page miss、read_file missing_resource、模型 fallback 等中间失败不再刷屏。
- 真正最终失败仍可通过 `error` event 或 `terminal=true` 工具失败摘要对用户展示。
