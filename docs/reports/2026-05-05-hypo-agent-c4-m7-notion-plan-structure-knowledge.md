# C4 M7 - Notion Plan 结构审计与 Knowledge 固化

## 结果

- 新增 `NotionPlanEditor.discover_structure()`，可读取计划通页面下的月份页结构。
- 真实只读 smoke 成功：解析到 `plan_page_id=c59c1409-f691-4920-87af-1e0e840cfc02`，发现 46 个月份页。
- 已生成本地 knowledge：
  - `memory/knowledge/notion-plan/structure.json`
  - `memory/knowledge/notion-plan/structure.md`

## 结构规则

- 默认日期标题格式：`{month}月{day}日`。
- 学期 anchor 已写入 knowledge：`大一上 = 2021-09`，`研一上 = 2025-09`。
- 月份页优先沿用真实标题；真实页面中 `2026-05` 标题为 `五月`。

## 验证

- `uv run pytest tests/core/test_notion_plan_reader.py tests/core/test_notion_plan_editor.py -q`
- 真实只读 smoke：`notion_plan_get_structure` 返回 `status=success`、`month_pages=46`。

## 残余风险

- 当前 structure discovery 主要记录直接月份页；更复杂嵌套结构仍依赖既有 reader 的 fallback。

