---
name: "agent-search"
description: "通过 Tavily 执行 Web search 与 page reading。用户需要在线检索、事实核验或读取公开网页时使用。"
compatibility: "linux"
allowed-tools: "web_search web_read"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "agent_search"
  hypo.exec_profile:
  hypo.triggers: "搜索,查找,搜一下,search,google,网上,看看,查询,找找,什么是,怎么回事"
  hypo.risk: "low"
  hypo.dependencies: "tavily-python"
---

# Agent Search 使用指南

## 定位 (Positioning)

`agent-search` 负责仓库外信息获取，提供 `Web search` 与页面正文提取能力，适合在线检索、事实核验和指定网页阅读。

## 适用场景 (Use When)

- 答案依赖当前 workspace 之外的公开信息。
- 用户要求核验事实、查资料或读取公开网页。
- 已有 URL，或需要先搜索再锁定候选页面。

## 工具与接口 (Tools)

- `web_search`：发现信息源、筛选候选页面。
- `web_read`：读取指定 URL 的正文内容。

## 标准流程 (Workflow)

1. 默认先从 `web_search` 开始，写具体 query。
2. query 中尽量包含 `topic`、`entity`、`date`、`version`、`site`、`region` 等限定词。
3. 首轮把 `max_results` 控制在 `3` 到 `5`，先看标题、snippet 和 URL。
4. 只对真正相关的一两页调用 `web_read`。
5. 输出时以总结和核验结论为主，不要整段转储原文。

## 参数约定 (Parameters)

- `web_search.query` 应写成具体搜索短语，而不是模糊问题。
- `web_search.max_results` 默认用 `3` 到 `5`，只有第一轮明显不够时再放大。
- `web_read.url` 应传最终要总结或引用的公开页面地址。

## 边界与风险 (Guardrails)

- 不要对大量 URL 盲读；先搜索再筛选。
- `snippet` 只用于初筛，不应替代最终核验。
- 公开网页提取结果可能有噪音，关键事实要结合来源可信度再总结。
