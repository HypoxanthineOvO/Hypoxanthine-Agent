---
name: "info-portal"
description: "Query Hypo-Info for today's news digest, topic search, AI benchmark data, and section browsing. Use when user asks about news, trends, or model benchmarks."
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

# Info Portal 使用说明

这个 skill 用于 Hypo-Info 内的被动信息查询。当用户询问今天的新闻、最近趋势、AI 排行或有哪些 section 可看时，它是默认选择。

## 工具选择

- 用 `info_today` 获取今天内容的简要 digest。可选传入 `section`，例如 `AI`、`开源`、`Cryo` 或其他已知 section。
- 当用户给出 topic 或 keyword，希望查相关文章时，用 `info_search`。
- 当用户询问 model ranking、benchmark score 或 leaderboard 对比时，用 `info_benchmark`。
- 当你需要先知道有哪些 section，再决定查询范围时，用 `info_sections`。

## 推荐调用顺序

1. 如果用户只是泛泛地问有哪些领域，或你不确定该查哪个 section，先调用 `info_sections`。
2. 日报类请求优先用 `info_today`。
3. 定向主题查询用 `info_search`。
4. `info_benchmark` 只用于 benchmark 和 ranking 问题。

## 参数说明

### `info_today`

- `section` 为可选参数。
- 留空时表示获取通用首页 digest。
- 当用户明确想看某个 section 时再设置它。

### `info_search`

- `query` 应该是核心 topic、person、model、company 或 event。
- `limit` 通常保持在 `5` 到 `10`。只有用户明确要求更广泛搜索时再提高。

### `info_benchmark`

- `top_n` 控制返回多少个排名模型。
- 默认用 `5` 或 `10`，除非用户要求更多。

### `info_sections`

- 无参数。
- 用它先了解合法的 section 名称，再做过滤。

## 回复方式

- `info_today` 应该按 digest 形式呈现，把最相关的条目放前面。
- `info_search` 应该先给出精炼的匹配列表；如果结果较多，再补充整体规律总结。
- `info_benchmark` 应该按 ranking summary 呈现，并指出最值得注意的领先者或分差。
- 使用 `info_sections` 后，不要只原样列出 section；要把这些结果转化为你接下来会怎么查，或用户可以选什么。

## 常见流程

### 每日新闻请求

1. 调用 `info_today`。
2. 总结最重要的条目。

### 主题查询

1. 用用户主题调用 `info_search`。
2. 总结最强相关的结果。

### 模型排行请求

1. 调用 `info_benchmark`。
2. 解释顶部结果和明显的分数差异。

### Section 发现

1. 调用 `info_sections`。
2. 选出相关 section。
3. 再调用 `info_today` 或 `info_search`。
