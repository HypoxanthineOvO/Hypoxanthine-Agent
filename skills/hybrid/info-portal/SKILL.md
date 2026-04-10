---
name: "info-portal"
description: "查询 Hypo-Info 的今日资讯、topic search、AI benchmark 与 section browse。用户询问 news、trends 或 model leaderboard 时使用。"
compatibility: "linux"
allowed-tools: "info_today info_search info_benchmark info_sections"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "info"
  hypo.exec_profile:
  hypo.triggers: "资讯,新闻,今天,热点,benchmark,模型榜,排行,AI,趋势,评测,分数,板块"
  hypo.risk: "low"
  hypo.dependencies: "hypo-info-api"
---

# Info Portal 使用指南

## 定位 (Positioning)

`info-portal` 是 Hypo-Info 的被动查询入口，覆盖今日 digest、topic search、`AI benchmark` 与 `section browse`。

## 适用场景 (Use When)

- 用户要看今天新闻、最近趋势、AI 榜单或可浏览的资讯板块。
- 问题是“我现在想查什么”，而不是“以后持续订阅什么”。

## 工具与接口 (Tools)

- `info_today`：获取今日 digest，可选按 `section` 过滤。
- `info_search`：按 topic 或 keyword 查询相关文章。
- `info_benchmark`：查看 model ranking 与 benchmark 数据。
- `info_sections`：列出可用资讯板块。

## 标准流程 (Workflow)

1. 不确定板块时，先用 `info_sections` 建立范围感。
2. 每日资讯优先走 `info_today`。
3. 定向主题查询走 `info_search`。
4. 排行榜和 benchmark 相关问题只走 `info_benchmark`。
5. 回复时把原始结果整理成 digest、summary 或 ranking insight，而不是机械转述字段。

## 参数约定 (Parameters)

- `info_today.section` 留空表示首页 digest；只有用户明确指定板块时再传。
- `info_search.query` 应聚焦核心 `topic`、`person`、`company`、`model` 或 `event`。
- `info_search.limit` 一般控制在 `5` 到 `10`。
- `info_benchmark.top_n` 默认可用 `5` 或 `10`，除非用户要求更多。

## 边界与风险 (Guardrails)

- `info-portal` 面向被动查询，不负责 subscription management。
- 使用 `info_sections` 后，不要只原样列出板块；要转化成下一步查询建议。
- benchmark 输出应突出领先者、差距和变化点，而不是只给表格。
