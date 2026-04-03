# Notion Property Type Mapping 对照

backend 会把简单的 JSON 对象转换成 `notion_update_page` 和 `notion_create_entry` 所需的 Notion API property payload。

| Notion property type | JSON 输入示例 | Backend conversion |
| --- | --- | --- |
| `title` | `"Name": "Project Alpha"` | `{"title": [{"type": "text", "text": {"content": "Project Alpha"}}]}` |
| `rich_text` | `"Summary": "Draft spec"` | `{"rich_text": [{"type": "text", "text": {"content": "Draft spec"}}]}` |
| `select` | `"Priority": "High"` | `{"select": {"name": "High"}}` |
| `status` | `"Status": "In Progress"` | `{"status": {"name": "In Progress"}}` |
| `multi_select` | `"Tags": ["AI", "Agent"]` | `{"multi_select": [{"name": "AI"}, {"name": "Agent"}]}` |
| `number` | `"Estimate": 3` | `{"number": 3}` |
| `checkbox` | `"Done": true` | `{"checkbox": true}` |
| `date` | `"Date": "2026-04-03"` | `{"date": {"start": "2026-04-03"}}` |
| `date` with range/object | `"Date": {"start": "2026-04-03", "end": "2026-04-04"}` | Passed through as `{"date": {...}}` |
| `url` | `"Link": "https://example.com"` | `{"url": "https://example.com"}` |
| `email` | `"Owner Email": "user@example.com"` | `{"email": "user@example.com"}` |
| `phone_number` | `"Phone": "123456"` | `{"phone_number": "123456"}` |

## 不支持的直接更新

这些 property type 不能由 backend 直接更新，调用时会报错：

- `rollup`
- `created_time`
- `last_edited_time`
- `formula`

## 推断回退

如果 schema 缺失，backend 会回退到基础类型推断：

- `Name` 或 `Title` 这类 property name 会推断为 `title`
- boolean 会推断为 `checkbox`
- number 会推断为 `number`
- list 会推断为 `multi_select`
- 其他情况都会回退到 `rich_text`
