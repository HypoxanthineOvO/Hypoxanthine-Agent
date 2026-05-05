# C4 M6 端到端回归与体验验收

## 验收结论

M2-M5 核心路径已通过 mock/e2e 验收。用户可见体验已从“中间失败刷屏 / 泛化 LLM 调用失败 / QQ 图文丢图”收敛为“折叠调用中 -> 最终成功或一次失败摘要”。

## M1 案例回放

| M1 案例 | 回放方式 | 结果 |
|---|---|---|
| Notion schema mismatch | `test_channel_progress_final_failure_uses_display_summary`、WebUI final failure 测试 | 最终摘要显示 `查询 Notion 失败`、attempts、`schema_mismatch` |
| read_file missing artifact | `test_classify_tool_error_for_m1_cases`、WebUI retryable error 测试 | retry 中间失败不外显，错误归类 `missing_resource` |
| 泛化 LLM 失败 | `test_fallback_exhausted_raises_structured_error`、`test_pipeline_emits_structured_model_fallback_error` | 最终错误为 `LLM_FALLBACK_EXHAUSTED`，包含 attempted chain |
| QQBot 图文丢图 | `test_qqbot_handle_event_accepts_c2c_text_and_image`、WS 图文测试 | Pipeline 收到 text 和 image attachment |
| 图片生成 traceback | `test_summarize_tool_failure_hides_traceback_shape` | traceback 被压缩为用户摘要，不直接刷屏 |

## 验证命令

- `uv run pytest tests/core/test_tool_display.py tests/core/test_channel_adapter.py tests/core/test_channel_progress.py tests/core/test_model_router.py tests/unit/test_pipeline_error_handling.py tests/gateway/test_qqbot_channel.py tests/gateway/test_qqbot_ws_channel.py -q`：56 passed。
- `uv run pytest tests/core/test_model_router.py tests/unit/test_pipeline_error_handling.py tests/core/test_pipeline.py -q`：83 passed。
- `uv run pytest tests/gateway/test_qqbot_channel.py tests/gateway/test_qqbot_ws_channel.py -q -m integration`：26 passed。
- `uv run pytest tests/channels/test_feishu_channel.py tests/channels/test_weixin_channel.py -q`：22 passed。
- `uv run pytest tests/core/test_pipeline_event_consumer.py tests/gateway/test_ws_echo.py tests/core/test_channel_adapter.py tests/core/test_channel_progress.py tests/core/test_tool_display.py -q`：34 passed。
- `uv run pytest tests/core/test_resource_resolution.py tests/core/test_pipeline_tools.py -q`：51 passed。
- `npm test -- src/composables/__tests__/useChatSocket.spec.ts src/views/__tests__/ChatView.spec.ts`：40 passed。
- `npm run build`：passed。

## 残余风险

- QQBot 真实平台 payload 可能还有未见字段；当前实现容忍多种字段并保留 unresolved diagnostics，但仍建议上线后观察 `metadata.qq.unresolved_attachments`。
- stream 已输出后失败仍无法无缝续写，只能给结构化最终摘要。
- 工具展示 registry 覆盖了 M1 高频工具；后续新增工具需要同步 registry 或依赖 fallback。
