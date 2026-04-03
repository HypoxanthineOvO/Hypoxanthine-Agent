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
# info-reach/SKILL Guide

Use this skill as described by the frontmatter description: TrendRadar proactive intelligence: query aggregated info, get summaries, and manage topic subscriptions for automatic push notifications.

## Tools

- Allowed tools: info_query info_summary info_subscribe info_list_subscriptions info_delete_subscription
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
