---
name: "info-reach"
description: "TrendRadar 主动资讯 workflow：结构化 query、summary 与 subscription push。用户要订阅、推送、跨多日检索或管理主题关注时使用。"
compatibility: "linux"
allowed-tools: "info_query info_summary info_subscribe info_list_subscriptions info_delete_subscription"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "info_reach"
  hypo.exec_profile:
  hypo.triggers: "订阅,推送,TrendRadar,资讯推送,关注,取消订阅,摘要,全局摘要"
  hypo.risk: "low"
  hypo.dependencies: "hypo-info-api,aiosqlite"
---

# Info Reach 使用指南

## 定位 (Positioning)

`info-reach` 面向主动资讯 workflow，覆盖结构化多日查询、聚合摘要与 `subscription push` 管理。

## 适用场景 (Use When)

- 用户要创建、查看或删除订阅。
- 需要更宽时间窗口，例如 `3d`、`7d`。
- 需要 `min_importance`、`source_name` 这类结构化过滤。
- 需求属于 proactive push，而不是单次被动查询。

## 工具与接口 (Tools)

- `info_query`：结构化文章查询。
- `info_summary`：按时间范围生成聚合 digest。
- `info_subscribe`：创建或更新订阅。
- `info_list_subscriptions`：查看当前订阅。
- `info_delete_subscription`：删除指定订阅。

## 标准流程 (Workflow)

1. 先判断需求是被动查询还是主动订阅；前者优先考虑 `info-portal`。
2. 结构化检索时，用 `info_query` 指定时间窗口和过滤条件。
3. 用户只想看压缩 overview 时，改用 `info_summary`。
4. 订阅前先确认 `topic`、频率和 `category`，再调用 `info_subscribe`。
5. 管理订阅时，先列出现有项，再做删除或调整。

## 参数约定 (Parameters)

- `info_query` 可选字段包括 `category`、`keyword`、`time_range`、`min_importance`、`source_name`。
- `info_summary.time_range` 常见值为 `today`、`yesterday`、`3d`、`7d`。
- `info_subscribe` 需要清晰的 `name`、`keywords`，可选 `categories` 与 `schedule`。
- `info_delete_subscription.name` 必须精确匹配要删除的订阅。

## 边界与风险 (Guardrails)

- 如果用户只是问“今天有什么新闻”，优先用 `info-portal`，不要过度升级到订阅 workflow。
- 结构化过滤会缩小结果集，使用前应确认用户是否真需要这些限制。
- 删除订阅前应尽量先展示当前订阅列表，降低误删风险。
