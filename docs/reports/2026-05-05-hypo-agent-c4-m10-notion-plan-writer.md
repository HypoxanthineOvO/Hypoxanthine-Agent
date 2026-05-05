# C4 M10 - Notion Plan 写入与结果回放

## 结果

- `notion_plan_add_items` 支持真实写入、dry-run preview、部分失败汇总和幂等跳过。
- 修复 `NotionClient.create_page()`：空 children 不再传 `children=None`。
- `NotionClient.append_blocks()` 支持 `after` cursor。
- 普通 `notion_create_entry` 空 content 不再触发 `children=null` validation error。

## 真实案例

输入：

```text
5/8 10:30-11:30 普拉提训练
```

真实 dry-run：

```text
5/8 10:30-11:30 普拉提训练 -> 五月 / 5月8日 / 位于 CS110 Lab 11 之前
```

真实写入：

```text
已加入计划通：5/8 10:30-11:30 普拉提训练
插入位置：五月 / 5月8日 / 位于 CS110 Lab 11 之前
当天日程：CS110 Lab 11；信号 HW 6；代码结构的重新规划
```

幂等复跑：

```text
计划通已存在，跳过重复：5/8 10:30-11:30 普拉提训练
插入位置：五月 / 5月8日 / 已存在，跳过重复写入
当天日程：10:30-11:30 普拉提训练；CS110 Lab 11；信号 HW 6；代码结构的重新规划
```

## Smoke 修正

首次 smoke 暴露了早于当天第一项时 `after` 为空会追加到月份页末尾的问题。已补红灯测试并修复为使用日期 heading 作为 anchor；污染的两条测试项已从 `5月31日` 删除，再按修复后路径写入。

## 验证

- `uv run pytest tests/channels/test_notion_client.py tests/skills/test_notion_skill.py tests/core/test_notion_plan_editor.py tests/skills/test_notion_plan_skill.py -q`

