# C4 M11 - Notion Plan 路由与端到端验收

## 结果

- Pipeline 新增高置信度计划通写入 shortcut。
- 当前消息包含可解析日期项目时，直接调用 `notion_plan_add_items`。
- 当前消息为“把这一条加到计划通”时，可从最近历史消息提取上一条日期项目。
- 不再让该真实案例误走 `notion_create_entry` 或 `create_reminder`。
- 工具展示映射新增 `notion_plan_get_today`、`notion_plan_get_structure`、`notion_plan_add_items`。

## 回放结果

真实案例：

```text
5/8 10:30-11:30 普拉提训练
把这一条加到Notion计划通子页面里对应位置
```

测试确认：

- 调用：`notion_plan_add_items`
- 参数：完整文本
- 未调用：`notion_create_entry`
- 未调用：`create_reminder`
- LLM 未参与高置信度路径

## 验证

- `uv run pytest tests/core/test_pipeline_tools.py tests/core/test_progressive_disclosure.py tests/core/test_tool_display.py -q`
- 全量相关回归：
  - `uv run pytest tests/core/test_notion_plan_reader.py tests/core/test_notion_plan_editor.py tests/skills/test_notion_plan_skill.py tests/channels/test_notion_client.py tests/skills/test_notion_skill.py tests/core/test_skill_catalog_repo.py tests/core/test_pipeline_tools.py tests/core/test_progressive_disclosure.py tests/core/test_tool_display.py -q`
  - 132 passed
- `git diff --check` 无输出。

## 残余风险

- 复杂自然语言、重复规则、修改/删除计划通 item 尚未实现。
- Notion API 没有 before cursor；早于当天第一条时必须使用日期 heading 作为 `after` anchor。
- 如果真实页面结构大幅变化，需要重新刷新 `memory/knowledge/notion-plan/structure.*`。

