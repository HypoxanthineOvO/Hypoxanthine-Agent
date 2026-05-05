# C4 M4 LLM 隐式 fallback 与失败摘要

## 结果

已替换泛化 `LLM 调用失败，请检查配置或稍后重试` 用户路径。模型失败会携带 attempted chain；最终失败显示模型链路摘要。

## 改动

- 新增 `ModelFallbackError`，包含 `requested_model`、`task_type`、`attempted_chain` 和 `retryable`。
- `ModelRouter.call_with_tools()` 与 `stream()` 为每个候选模型记录结构化 attempt，包括 provider、model_id、能力、失败类别、延迟和可重试性。
- `model_fallback` / `model_fallback_exhausted` 事件携带 `attempted_chain`；耗尽事件携带用户摘要。
- vision task 会跳过非 vision fallback，并继续尝试后续 vision-capable 模型。
- Pipeline 和 WebSocket gateway 捕获 `ModelFallbackError` 后返回 `LLM_FALLBACK_EXHAUSTED`，不再降级成泛化 LLM 文案。
- RuntimeError 兜底文案也改为 `模型调用失败：<原因>`。

## 限制

stream 已经输出后失败仍不能安全重放完整回答；当前做法是抛出结构化失败，摘要说明失败阶段和 attempted chain。

## 验证

- `tests/core/test_model_router.py` 覆盖 fallback success、fallback exhausted、vision fallback 跳过非 vision 模型。
- `tests/unit/test_pipeline_error_handling.py` 覆盖 Pipeline 结构化模型失败事件。
- `tests/gateway/test_ws_echo.py` 覆盖 WebSocket runtime/provider error 文案。
