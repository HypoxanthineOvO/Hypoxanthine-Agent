# C5/M3 调用中聚合器与最终回放

## 目标

减少渠道机械进度刷屏：同一轮任务中，如果由 `channel_progress` 兜底生成“正在调用...”文本，只发送第一条；后续工具 start 折叠为内部状态。

## 本轮改动

- `src/hypo_agent/core/channel_progress.py`
  - 恢复并使用 `prelude_sent` 参数。
  - 第一次 `tool_call_start` 返回 running text，并将 `prelude_sent=True`。
  - 后续 `tool_call_start` 返回 `None`，保持 `prelude_sent=True`。
  - narration 开启时仍由现有 narration/debounce 机制处理，不改变生产 narration 行为。

## 验证

- `uv run pytest tests/core/test_channel_progress.py tests/channels/test_weixin_channel.py tests/channels/test_feishu_channel.py -q`
- 结果：30 passed。

## 说明

这一步是轻量聚合器，先解决“多个调用中刷屏”的兜底路径。更完整的长任务/并发语义在 M5 通过非阻塞运行时处理。
