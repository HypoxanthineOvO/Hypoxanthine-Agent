---
name: "notion-plan"
description: "读取和编辑 HYX 的 Notion Plan / 计划通；当用户要查看、添加或插入计划通日期事项时使用。"
compatibility: "linux"
allowed-tools: "notion_plan_get_today notion_plan_get_structure notion_plan_add_items"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "notion"
  hypo.exec_profile:
  hypo.triggers: "计划通,Notion Plan,notion plan,加到计划通,插入计划通,添加计划通,今日计划,日期块,日程,今天,待办事项,查看一下今天的计划通待办事项"
  hypo.risk: "medium"
  hypo.dependencies: "notion-client"
---

# Notion Plan 使用指南

`notion-plan` 是 `HYX的计划通` 的专用能力，和普通 Notion page/database 操作分开。

## 适用场景

- 用户要查看今天的计划通。
- 用户发送日期、时间、内容，并要求加到计划通。
- 用户说“把这一条加到计划通”“插入到 Notion 计划通对应位置”。
- 用户要读取计划通真实页面结构。

## 工具

- `notion_plan_get_today`：读取今日计划通。
- `notion_plan_get_structure`：读取并固化计划通结构 knowledge。
- `notion_plan_add_items`：把一个或多个日期事项加入计划通。

## 规则

1. 计划通写入必须使用 `notion_plan_add_items`，不要用 `notion_create_entry` 或 `notion_write_page` 猜结构。
2. 高置信度日期项目可以直接写入。
3. 多条输入中成功项先写，失败项汇总。
4. 跨天事项写在开始日期。
5. 无时间事项放在当天有时间事项后面。
