---
name: "code-run"
description: "Sandboxed code execution for temporary scripts. Prefers bwrap isolation when available."
compatibility: "linux"
allowed-tools: "run_code"
metadata:
  hypo.category: "internal"
  hypo.backend: "code_run"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "medium"
  hypo.dependencies: "bash,python,bwrap(optional)"
---
# code-run/SKILL Guide

Use this internal skill as described by the frontmatter description: Sandboxed code execution for temporary scripts. Prefers bwrap isolation when available.

## Tools

- Allowed tools: run_code
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
