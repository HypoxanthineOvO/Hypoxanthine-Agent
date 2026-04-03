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
# info-portal/SKILL Guide

Use this skill as described by the frontmatter description: Query Hypo-Info for today's news digest, topic search, AI benchmark data, and section browsing. Use when user asks about news, trends, or model benchmarks.

## Tools

- Allowed tools: info_today info_search info_benchmark info_sections
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
