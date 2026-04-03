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
# agent-search/SKILL Guide

Use this skill as described by the frontmatter description: Web search and page content extraction via Tavily. Use when user needs to find information online, verify facts, or read a specific webpage.

## Tools

- Allowed tools: web_search web_read
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
