---
name: "email-scanner"
description: "Multi-account email scanning, classification, summarization, caching, and proactive push. IMAP-based with rule engine."
compatibility: "linux"
allowed-tools: "scan_emails search_emails list_emails get_email_detail"
metadata:
  hypo.category: "internal"
  hypo.backend: "email_scanner"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "imap,structured_store,email_rules.yaml"
---
# email-scanner/SKILL Guide

Use this internal skill as described by the frontmatter description: Multi-account email scanning, classification, summarization, caching, and proactive push. IMAP-based with rule engine.

## Tools

- Allowed tools: scan_emails search_emails list_emails get_email_detail
- Treat these tools as internal runtime contracts rather than user-facing branded workflows.

## Workflow

Use the listed internal primitives carefully, keep arguments explicit, and return normalized results.

## Safety

Preserve backend boundaries, avoid leaking internal payload details, and keep execution scoped to the request.
