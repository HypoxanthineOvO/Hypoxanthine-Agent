---
name: "reminder"
description: "Reminder CRUD with scheduler integration. Creates, lists, updates, deletes, and snoozes timed reminders."
compatibility: "linux"
allowed-tools: "create_reminder list_reminders delete_reminder update_reminder snooze_reminder"
metadata:
  hypo.category: "internal"
  hypo.backend: "reminder"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "structured_store,scheduler"
---

# Reminder 使用说明

这个 backend 管理 reminder 及其 scheduler job，负责 create、inspect、update、delete 和 snooze 流程。

## 典型流程

- 用 `create_reminder` 添加一个带有效未来时间的 reminder。
- 用 `list_reminders` 查看活动中或历史 reminder。
- 当 title、schedule、channel 或 status 需要变化时，用 `update_reminder`。
- 用 `delete_reminder` 删除 reminder，并取消对应 job。
- 当 reminder 已存在、用户只想短暂延后时，用 `snooze_reminder`。

## 时间构造规则

- 在调用 `create_reminder` 前，把“明天下午三点”这类相对表达转成绝对 ISO 8601 timestamp。
- 对 `once` reminder，要传绝对的未来 datetime string，而不是相对表达。
- 对周期性 reminder，在合适时使用 cron expression。
- backend 会校验一次性 reminder 不能落在过去。

## Timezone 处理

- 已知用户 timezone 时，优先使用它。
- 如果拿不到用户 timezone，在调度有歧义的 reminder 前，要明确说明你假设使用的 timezone。
- 不要静默混用用户本地意图和服务器本地 wall clock time。

## Snooze 说明

- `snooze_reminder` 用于像 `10m`、`2h`、`1d` 这样的快速偏移。
- 当 reminder 已存在，而且用户只想短暂延后、而不是完全改计划时，用它。

## 安全规则

- 当精度重要时，不要猜测有歧义的日期或时间。
- 如果时间表述不清楚，先澄清再调度。
- 当目标 reminder 不够明确时，优先先列出或预览 reminder，再做破坏性变更。
