---
name: "reminder"
description: "Reminder CRUD with scheduler integration. Creates, lists, updates, deletes, and snoozes timed reminders."
compatibility: "linux"
allowed-tools: "create_reminder list_reminders delete_reminder update_reminder snooze_reminder"
metadata:
  hypo.category: "internal"
  hypo.backend: "reminder"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "structured_store,scheduler"
---
# reminder/SKILL Guide

Use this internal skill as described by the frontmatter description: Reminder CRUD with scheduler integration. Creates, lists, updates, deletes, and snoozes timed reminders.

## Tools

- Allowed tools: create_reminder list_reminders delete_reminder update_reminder snooze_reminder
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
