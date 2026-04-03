---
name: "tmux"
description: "Persistent terminal session management. Legacy primitive, prefer exec for one-shot commands."
compatibility: "linux"
allowed-tools: "tmux_send tmux_read"
metadata:
  hypo.category: "internal"
  hypo.backend: "tmux"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "medium"
  hypo.dependencies: "tmux"
---
# tmux/SKILL Guide

Use this internal skill as described by the frontmatter description: Persistent terminal session management. Legacy primitive, prefer exec for one-shot commands.

## Tools

- Allowed tools: tmux_send tmux_read
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
