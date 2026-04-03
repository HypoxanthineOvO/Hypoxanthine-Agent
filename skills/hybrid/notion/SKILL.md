---
name: "notion"
description: "Read and write Notion pages and databases. Use when user wants to create, query, update, or search content in their Notion workspace."
compatibility: "linux"
allowed-tools: "notion_get_schema notion_read_page notion_write_page notion_update_page notion_query_db notion_create_entry notion_search"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "notion"
  hypo.exec_profile:
  hypo.triggers: "notion,笔记,页面,数据库,记录,写入,创建页面,查询,工作区,写到notion,读取页面"
  hypo.risk: "medium"
  hypo.dependencies: "notion-client"
---

# Notion 使用说明

当用户想在已连接的 Notion workspace 中读取、搜索、创建或更新内容时，使用这个 skill。

## 工具选择

- 在写入 database entry、更新 properties，或为 database query 组装 `filter` / `sorts` 之前，先用 `notion_get_schema`。
- 用 `notion_read_page` 读取页面 metadata 和类 Markdown 的正文内容。
- 用 `notion_write_page` 对现有页面正文做追加或替换。
- 用 `notion_update_page` 通过 JSON 对象更新页面 properties。
- 用 `notion_query_db` 按 Notion API 风格的 `filter` 和 `sorts` 查询 database。
- 用 `notion_create_entry` 在 database 内创建新页面，也可附带 markdown 内容。
- 当你只有关键词、还不知道 page 或 database 的具体对象时，用 `notion_search` 先找目标。

## 推荐流程

1. 如果目标 database 结构未知，先调用 `notion_get_schema`。
2. 读取页面时用 `notion_read_page`。
3. 修改正文时用 `notion_write_page`。
4. 修改属性时用 `notion_update_page`。
5. 处理 database 行时，用 `notion_query_db` 或 `notion_create_entry`。
6. 如果用户不知道精确 page ID 或 database ID，就从 `notion_search` 开始。

## 参数说明

### `notion_get_schema`

- `database_id`：必填。使用准确的 database ID。

### `notion_read_page`

- `page_id`：可以传 Notion page ID，也可以传完整 page URL。

### `notion_write_page`

- `page_id`：目标页面 ID 或 URL。
- `content`：要写入的 Markdown 内容。
- `mode`：默认使用 `append`。只有在确实要替换可编辑正文 block 时才用 `replace`。

### `notion_update_page`

- `page_id`：目标页面 ID 或 URL。
- `properties`：以字符串编码的 JSON 对象。key 应与真实 Notion property 名称一致。

### `notion_query_db`

- `database_id`：必填。
- `filter`：可选的 Notion API filter JSON。
- `sorts`：可选的 Notion API sorts JSON array。
- `limit`：返回行数。

### `notion_create_entry`

- `database_id`：必填。
- `properties`：以字符串编码的 JSON 对象。必须精确匹配 database schema。
- `content`：可选的创建后页面 markdown 正文。

### `notion_search`

- `query`：搜索关键词。
- `type`：`page` 或 `database`。

## Property Type Mapping 说明

发送 property JSON 之前，先确认 database schema。backend 会把常见的 Python/JSON 值转换成 Notion property payload。

支持的映射与示例见 `references/property-types.md`。

## 常见流程

### 读取页面

1. 调用 `notion_read_page`。
2. 同时总结页面 properties 和正文内容。

### 查询任务数据库

1. 如果 schema 未知，先用 `notion_get_schema`。
2. 使用 `filter` 和 `sorts` payload 调用 `notion_query_db`。

### 创建新条目

1. 调用 `notion_get_schema`。
2. 构造匹配 schema 的 `properties` JSON。
3. 调用 `notion_create_entry`。

### 更新现有条目

1. 先用 `notion_read_page` 或 `notion_get_schema` 确认 property 名称。
2. 用 `notion_update_page` 更新 properties。
3. 如果正文也要改，再调用 `notion_write_page`。
