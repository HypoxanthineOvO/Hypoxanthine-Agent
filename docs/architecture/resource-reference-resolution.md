# Resource Reference Resolution

Date: 2026-05-03
Scope: C1-M2

## Goal

Resource resolution is the shared contract for turning user-facing references into concrete resources before tool execution or channel delivery.

The primary user path is channel-first:

```text
用户说“把刚才那个报告发到微信/QQ/飞书”
  -> resolve resource reference
  -> confirm when ambiguous
  -> pass ResourceRef to channel delivery
  -> delivery layer checks channel capability and sends/falls back
```

WebUI support is secondary. The same contract should remain renderable by WebUI, but M2 optimizes for QQ/微信/飞书 delivery and future proactive recovery.

## Core Types

`ResourceRef`

- `kind`: `file`, `attachment`, `url`, `webpage`, `generated_file`
- `uri`: concrete path or URL
- `display_name`: human-readable label
- `mime_type`, `size_bytes`
- `metadata`: purpose and source-specific fields

`ResourceCandidate`

- wraps a `ResourceRef`
- includes `score`, `source`, and `reason`
- used when the system finds one or more likely resources

`ResourceResolution`

- `resolved`: exactly one resource was selected
- `ambiguous`: multiple candidates require confirmation
- `not_found`: no candidate was found
- `blocked`: reserved for permission or policy blocks

`ResourceRecoveryAction`

- `ask_user`: user must choose from candidates
- `search_or_ask`: system can search broader context or ask for clarification
- `request_permission`: reserved for permission-limited paths

## Current Implementation

The initial implementation lives in `src/hypo_agent/core/resource_resolution.py`.

It supports:

- exact absolute/relative paths
- fuzzy filename search under configured roots
- recent generated files
- recent uploaded/message attachments
- HTTP/HTTPS URLs

`FileSystemSkill.read_file` now uses the resolver when a requested path does not exist. Instead of only returning `File not found`, it adds:

- `metadata.resource_resolution`
- `metadata.resource_candidates`
- `metadata.recovery_action`

`ToolOutcome` treats missing-resource errors with an explicit recovery action as retryable user-input errors. This keeps the circuit breaker from punishing recoverable ambiguity and gives M3 a structured handoff.

## Channel Delivery Implications

M2 does not implement channel capability checks. It prepares the input contract for M5:

- channel delivery should receive a concrete `ResourceRef`, not raw text like `"那个报告"`;
- ambiguous resources must be confirmed before sending;
- generated files and uploaded attachments should be treated the same way by delivery;
- missing resources should produce a recovery action, not a low-quality final answer.

M5 should add `ChannelCapability` and per-attachment `DeliveryResult` details.

## Active Recovery Dependency

M6 should build the active recovery state machine on top of these M2 results:

```text
resolve_resource
  -> validate_channel_capability
  -> send_or_upload
  -> retry_or_fallback
  -> verify_delivery
  -> give_up_explained
```

The resolver does not decide when to retry, ask, fallback, or stop. It only returns resource facts and recovery options.

## Test Coverage

Current focused coverage:

- `tests/core/test_resource_resolution.py`
- `tests/skills/test_fs_skill.py::test_read_file_returns_resource_recovery_action_for_missing_file`
- `tests/core/test_tool_outcome.py`

Covered cases:

- exact file path for channel delivery
- recent generated report by fuzzy name
- ambiguous recent attachments requiring confirmation
- missing resource recovery action
- URL modeling
- `read_file` missing-path recovery metadata
- retryable `ToolOutcome` when recovery action exists

Remaining work:

- pass recent session messages from pipeline into resolver context;
- add explicit permission-blocked `ResourceResolution`;
- add channel delivery integration in M5;
- expose candidate confirmation through non-WebUI channels.
