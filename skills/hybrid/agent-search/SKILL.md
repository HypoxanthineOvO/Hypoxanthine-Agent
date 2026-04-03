---
name: "agent-search"
description: "Web search and page content extraction via Tavily. Use when user needs to find information online, verify facts, or read a specific webpage."
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

# Agent Search 使用说明

当答案依赖当前仓库之外的信息，或用户要求你读取公开网页时，使用这个 skill。

## 工具选择

- 需要发现信息源、核验事实或寻找候选页面时，先用 `web_search`。
- 已经有 URL、只需要读取页面正文时，用 `web_read`。
- 不要盲目对很多 URL 直接调用 `web_read`。先搜索，再读真正相关的一两页。

## 搜索策略

1. 从 `web_search` 开始。
2. 写具体的查询词，包含 topic、entity，以及 date、version、site、region 等相关限定词。
3. 默认把 `max_results` 控制得较小。除非确实需要广泛调研，否则用 `3` 到 `5`。
4. 先看标题、摘要和 URL，只读取最相关的结果页。

## 参数说明

### `web_search`

- `query`：写具体搜索短语，不要写模糊问题。
- 好例子：
  - `OpenAI GPT-5.4 API docs`
  - `site:docs.python.org asyncio subprocess timeout`
  - `2026 AI benchmark leaderboard`
- `max_results`：优先使用 `3` 到 `5`。只有在第一轮明显不够时再增大。

### `web_read`

- `url`：传入你要总结或引用的最终页面 URL。
- 适用于文章正文、文档页面和其他可读性较好的公开网页。

## 结果解读

- `web_search` 会返回排序后的结果，包含 `title`、`url`、`content`、`score`，以及可选的 `favicon`。
- 用 snippet 内容判断结果是否值得继续阅读。
- `web_read` 会返回适合 Markdown 阅读的提取正文。除非用户明确要求原文，否则应先整理总结，不要直接整段转储。

## 常见流程

### 快速事实核验

1. 用精确 query 调用 `web_search`。
2. 选择最可信的结果。
3. 只有当 snippet 不够时，再调用 `web_read`。

### 主题调研

1. 用范围适中的 query 调用 `web_search`。
2. 对比前几条结果。
3. 用 `web_read` 读取一到两页权威来源。
4. 综合整理结论。

### 读取指定页面

1. 如果用户已经给了 URL，直接用 `web_read`。
2. 如果 URL 缺失或有歧义，先搜索。
