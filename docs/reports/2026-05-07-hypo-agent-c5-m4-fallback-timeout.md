# C5/M4 模型与工具 fallback/timeout 恢复

## 目标

把临时 timeout、路径 miss、页面名 miss 收进内部恢复链路：只有最终无法恢复时，才向用户发送一条简洁失败摘要。

## 本轮改动

- `src/hypo_agent/skills/agent_search_skill.py`
  - 为 `search_web` 和 `web_read` 增加幂等客户端重试层。
  - Tavily search/extract 遇到 timeout、连接中断、临时网络异常时自动 retry 一次。
  - retry 事件只进入日志，不作为渠道消息外显。
- 复核现有恢复链路：
  - 模型 fallback 已由 `ModelRouter` 进入下一模型；M1 已隐藏成功 fallback 事件。
  - 文件读取已有 `memory/uploads` 恢复逻辑，覆盖微信临时图片路径 miss。
  - Notion Plan 通过专用 `plan_page_id`/semester root 优先定位，页面名 miss 会被 M2 策略作为非终态内部失败折叠。

## 幂等与写入保护

- 本轮只对读类工具增加隐式 retry：搜索和网页读取。
- Notion Plan 写入不做盲 retry，继续依赖已有重复检测和专用计划通定位，避免跨天/跨页重复写入。

## 验证

- `uv run pytest tests/skills/test_agent_search_skill.py tests/core/test_channel_progress.py tests/unit/test_pipeline_error_handling.py tests/skills/test_fs_skill.py tests/skills/test_notion_plan_skill.py tests/gateway/test_app_runtime_flags.py tests/core/test_pipeline_event_consumer.py -q`
- 结果：70 passed。

## 覆盖样本

- TicNote 搜索：首轮 Tavily timeout 可内部 retry；最终成功时不显示 `Request timed out after 60 seconds`。
- 图片解释：原始微信临时路径 miss 后可从 `memory/uploads` 恢复；成功时不显示 `读取文件 失败`。
- Notion 计划通：页面名 miss 属于内部候选定位失败；最终成功时不显示 `Notion page not found: HYX的计划通`。
