# C5/M5 异步消息执行与渠道输出解耦

## 目标

让渠道入站消息快速入队，后台执行长任务；渠道只订阅稳定状态和最终结果，不被内部 retry/fallback 阻塞或刷屏。

## 本轮改动

- `src/hypo_agent/gateway/app.py`
  - 将已有非阻塞 runtime 改为默认启用。
  - 保留 `HYPO_NONBLOCKING_RUNTIME=0/false/no/off/disabled` 作为显式关闭开关。
- 复核现有 runtime：
  - `enqueue_user_message()` 返回稳定 `work_id`。
  - event consumer 只负责调度 work item。
  - 每个 session 内保持顺序，不同 session 可并发。
  - work status 使用 `queued/running/done/error/timeout/cancelled` 终态。

## 验证

- `uv run pytest tests/skills/test_agent_search_skill.py tests/core/test_channel_progress.py tests/unit/test_pipeline_error_handling.py tests/skills/test_fs_skill.py tests/skills/test_notion_plan_skill.py tests/gateway/test_app_runtime_flags.py tests/core/test_pipeline_event_consumer.py -q`
- 结果：70 passed。

## 行为说明

- 长搜索、Notion 读取、图片解释等任务进入后台执行路径。
- 纯对话不经过工具调用时不会生成“调用中”兜底文本。
- 同一用户的下一条消息仍按 session 顺序排队；不同 session 不会被慢任务拖住。
