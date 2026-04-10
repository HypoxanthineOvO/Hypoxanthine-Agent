---
name: "reminder"
description: "Reminder CRUD 与 scheduler integration：创建、列出、更新、删除与 snooze 定时提醒。"
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

# Reminder 使用指南

## 定位 (Positioning)

`reminder` 负责 reminder 的 CRUD 与 `scheduler integration`，覆盖创建、查看、更新、删除与 `snooze` 流程。

## 适用场景 (Use When)

- 用户要新增定时提醒或周期提醒。
- 用户要查看、修改、删除已有 reminder。
- 用户只想把已有 reminder 暂时顺延，而不是重写完整计划。

## 工具与接口 (Tools)

- `create_reminder`：创建新的 reminder。
- `list_reminders`：查看活动中或历史 reminder。
- `update_reminder`：更新 title、schedule、channel 或 status。
- `delete_reminder`：删除 reminder，并取消对应 scheduler job。
- `snooze_reminder`：按短偏移量顺延已有 reminder。

## 标准流程 (Workflow)

1. 先把自然语言时间转换成明确的绝对时间或 `cron expression`。
2. 已知用户 `timezone` 时优先使用它；未知时要显式说明假设。
3. 新建提醒走 `create_reminder`，已有提醒的小幅顺延走 `snooze_reminder`。
4. 当目标 reminder 不够明确时，先用 `list_reminders` 定位，再做更新或删除。

## 参数约定 (Parameters)

- 一次性 reminder 的时间应传未来的绝对 `ISO 8601 timestamp`。
- 周期性 reminder 适合使用 `cron expression`。
- `snooze_reminder` 适合 `10m`、`2h`、`1d` 这类短偏移字符串。

## 边界与风险 (Guardrails)

- 时间精度重要时，不要猜测含糊日期；必要时先澄清。
- 不要静默混用用户本地意图与服务器本地 wall clock time。
- 破坏性操作前优先先确认目标 reminder，避免误删或误改。
