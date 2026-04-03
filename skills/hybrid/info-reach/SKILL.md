---
name: "info-reach"
description: "TrendRadar proactive intelligence: query aggregated info, get summaries, and manage topic subscriptions for automatic push notifications."
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

# Info Reach 使用说明

这个 skill 用于主动信息流工作流：结构化多日查询、聚合摘要，以及自动推送相关的订阅管理。

## 与 Info Portal 的边界

- `info-portal` 是普通用户查询新闻时的默认选择，例如今日 digest、section 浏览、keyword 搜索和 benchmark ranking。
- 当你需要下面这些能力时，应该选 `info-reach`：
  - subscription management
  - proactive push workflows
  - 更宽的时间窗口，例如 `3d` 或 `7d`
  - `min_importance`、`source_name` 这类结构化过滤条件

如果用户只是问“今天有什么新闻”或“AI 板块最近有什么”，优先用 `info-portal`。

## 工具选择

### 主动查询工具

- 用 `info_query` 做结构化文章查询，可选带上 `category`、`keyword`、`time_range`、`min_importance` 和 `source_name`。
- 用 `info_summary` 获取某个时间范围内的聚合 digest。

### 订阅工具

- 用 `info_subscribe` 创建或更新周期性订阅。
- 用 `info_list_subscriptions` 查看当前订阅。
- 用 `info_delete_subscription` 删除订阅。

## 参数说明

### `info_query`

- `category`：可选的高层 category 过滤。
- `keyword`：可选的关键词过滤。
- `time_range`：可选值为 `today`、`yesterday`、`3d` 或 `7d`。
- `min_importance`：可选的重要性阈值过滤。
- `source_name`：当用户只想看单一来源时，可选使用的 source 过滤。

### `info_summary`

- `time_range`：可选值为 `today`、`yesterday`、`3d` 或 `7d`。
- 当用户想看压缩后的 overview，而不是逐篇文章细节时使用它。

### `info_subscribe`

- `name`：订阅名。
- `keywords`：必填关键词列表。
- `categories`：可选分类列表。
- `schedule`：周期，如 `daily`。

### `info_list_subscriptions`

- 无参数。

### `info_delete_subscription`

- `name`：要删除的订阅名。

## 常见流程

### 结构化近期新闻查询

1. 用更宽的 `time_range`，例如 `3d` 或 `7d`，调用 `info_query`。
2. 当用户只关心重要事项时，加上 `min_importance`。
3. 总结返回的文章。

### 聚合 digest

1. 调用 `info_summary`。
2. 用精炼 overview 呈现 highlight、sections 和 counts。

### 创建订阅

1. 先确认 topic、频率和 category。
2. 调用 `info_subscribe`。
3. 确认已保存的订阅细节。

### 管理订阅

1. 用 `info_list_subscriptions` 展示当前订阅。
2. 当用户要删除某项订阅时，用 `info_delete_subscription`。
