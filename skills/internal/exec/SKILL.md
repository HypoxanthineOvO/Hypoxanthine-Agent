---
name: "exec"
description: "Core shell command execution. The runtime primitive behind all CLI-based Skills."
compatibility: "linux"
allowed-tools: "exec_command exec_script"
metadata:
  hypo.category: "internal"
  hypo.backend: "exec"
  hypo.exec_profile: "default"
  hypo.triggers: ""
  hypo.risk: "high"
  hypo.dependencies: "bash,python"
---
# exec/SKILL Guide

Use this internal skill as described by the frontmatter description: Core shell command execution. The runtime primitive behind all CLI-based Skills.

## Tools

- Allowed tools: exec_command exec_script
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
