---
name: "notion"
description: "读取与写入 Notion page / database。用户要在 Notion workspace 中 search、query、create 或 update 内容时使用。"
compatibility: "linux"
allowed-tools: "notion_get_schema notion_read_page notion_export_page_markdown notion_write_page notion_update_page notion_query_db notion_create_entry notion_search"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "notion"
  hypo.exec_profile:
  hypo.triggers: "notion,笔记,页面,数据库,记录,写入,创建页面,查询,工作区,写到notion,读取页面,导出,markdown,md,计划通,待办,事项,任务"
  hypo.risk: "medium"
  hypo.dependencies: "notion-client"
---

# Notion 使用指南

## 定位 (Positioning)

`notion` 用于在已连接的 Notion workspace 中读取、搜索、创建与更新 `page` 和 `database` 内容。

## 适用场景 (Use When)

- 用户要读写 Notion 页面正文。
- 用户要查询、创建或更新 Notion database 条目。
- 用户只知道关键词，不知道具体 page / database 对象。

## 工具与接口 (Tools)

- `notion_get_schema`：读取 database schema。
- `notion_read_page`：读取页面 metadata 和正文。
- `notion_export_page_markdown`：把 Notion 页面导出成 `.md` 文件附件。
- `notion_write_page`：追加或替换页面正文。
- `notion_update_page`：更新页面 `properties`。
- `notion_query_db`：按 `filter` / `sorts` 查询 database。
- `notion_create_entry`：创建 database 新条目。
- `notion_search`：按关键词搜索 page 或 database。

## 标准流程 (Workflow)

1. 如果目标 database 结构未知，先用 `notion_get_schema`。
2. 只读页面时用 `notion_read_page`。
3. 如果用户要“转成 md / markdown / 导出发我”，优先用 `notion_export_page_markdown`。
4. 改正文走 `notion_write_page`，改属性走 `notion_update_page`。
5. 处理 database 行时，用 `notion_query_db` 或 `notion_create_entry`。
6. 如果用户没有精确 ID，从 `notion_search` 开始定位对象；确认页面后再导出或读取。

## 参数约定 (Parameters)

- `notion_get_schema.database_id` 必填。
- `notion_read_page.page_id` 和 `notion_write_page.page_id` 可传 page ID 或完整 URL。
- `notion_export_page_markdown.page_id` 可传 page ID 或完整 URL；`filename` 只有在用户明确要求文件名时再传。
- `notion_write_page.mode` 默认用 `append`；只有明确要替换正文时才用 `replace`。
- `notion_update_page.properties` 与 `notion_create_entry.properties` 都必须匹配真实 schema。
- `notion_query_db.filter` / `sorts` 采用 Notion API 风格 JSON。

## 边界与风险 (Guardrails)

- 在写入 `properties` 前，先确认 schema，不要猜测字段名和类型。
- `properties` JSON 必须与真实 Notion property 名称一致。
- backend 会做部分 `property type mapping`，但这不是跳过 schema 检查的理由。
- 相关映射示例见 `references/property-types.md`。
