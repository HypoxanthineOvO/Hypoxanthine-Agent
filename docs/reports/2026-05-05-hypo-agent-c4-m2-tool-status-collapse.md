# C4 M2 工具调用状态折叠与最终失败摘要

## 结果

已实现工具调用用户侧折叠和最终失败摘要的基础协议。

## 改动

- 新增 `src/hypo_agent/core/tool_display.py`，提供工具展示名、运行文案、失败摘要和错误分类。
- `WebUIAdapter` 的 `tool_call_start` / `tool_call_result` 会携带 `display_name`、`running_text`、`success_text`、`failure_prefix`、`attempts`、`outcome_class`、`retryable` 和 `summary`。
- Pipeline 在工具重试事件中携带结构化字段；`will_retry=true` 的中间错误仍进日志和事件流，但用户侧不展示为失败消息。
- WebUI 对同一工具调用用稳定 key 更新同一 progress item；retryable error 返回 `null`，最终失败只展示一条摘要。
- 渠道进度 `channel_progress` 对 retryable error 继续静默，对最终失败使用摘要。

## 覆盖的 M1 案例

- Notion schema mismatch：最终摘要会显示 `查询 Notion 失败`、尝试次数和 `schema_mismatch`。
- read_file missing resource：归类为 `missing_resource`，retry 中间态不外显。
- exec_command allowlist/policy：归类为 `permission_or_policy`。
- generate_image traceback：归类为 `tool_runtime_error`，摘要会压缩错误，不直接刷 traceback。

## 验证

- `uv run pytest tests/core/test_tool_display.py tests/core/test_channel_adapter.py tests/core/test_channel_progress.py tests/core/test_model_router.py tests/unit/test_pipeline_error_handling.py tests/gateway/test_qqbot_channel.py tests/gateway/test_qqbot_ws_channel.py -q`：56 passed。
- `npm test -- src/composables/__tests__/useChatSocket.spec.ts`：21 passed。
- `uv run pytest tests/core/test_pipeline_event_consumer.py tests/gateway/test_ws_echo.py tests/core/test_channel_adapter.py tests/core/test_channel_progress.py tests/core/test_tool_display.py -q`：34 passed。
