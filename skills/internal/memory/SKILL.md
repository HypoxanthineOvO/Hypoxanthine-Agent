---
name: "memory"
description: "L2 preference memory: persist and retrieve user preferences and long-term context."
compatibility: "linux"
allowed-tools: "save_preference get_preference"
metadata:
  hypo.category: "internal"
  hypo.backend: "memory"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "structured_store"
---
# memory/SKILL Guide

Use this internal skill as described by the frontmatter description: L2 preference memory: persist and retrieve user preferences and long-term context.

## Tools

- Allowed tools: save_preference get_preference
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
