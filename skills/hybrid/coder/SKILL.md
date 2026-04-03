---
name: "coder"
description: "Delegate coding tasks to Hypo-Coder. Use when user requests code changes, feature implementation, bug fixes, or code review in a project managed by Hypo-Coder."
compatibility: "linux"
allowed-tools: "coder_submit_task coder_task_status coder_list_tasks coder_abort_task coder_health"
metadata:
  hypo.category: "hybrid"
  hypo.backend: "coder"
  hypo.exec_profile:
  hypo.triggers: "coder,编码,写代码,修复,实现,代码审查,提交任务,codex,开发任务,代码任务"
  hypo.risk: "medium"
  hypo.dependencies: "hypo-coder-api"
---
# coder/SKILL Guide

Use this skill as described by the frontmatter description: Delegate coding tasks to Hypo-Coder. Use when user requests code changes, feature implementation, bug fixes, or code review in a project managed by Hypo-Coder.

## Tools

- Allowed tools: coder_submit_task coder_task_status coder_list_tasks coder_abort_task coder_health
- Follow the listed tools in scope and summarize results for the user.

## Workflow

Use the hybrid backend intentionally, keep the tool sequence concrete, and explain results clearly.

## Safety

Stay within the exposed backend capability boundary and avoid unnecessary broad queries or writes.
