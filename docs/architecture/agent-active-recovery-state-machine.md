# Agent Active Recovery State Machine

Date: 2026-05-03
Scope: C1-M6

## Goal

Agent proactivity must be explicit, bounded, and testable. It should not rely on a prompt saying "be proactive".

For this cycle, the primary scenario is channel file delivery:

```text
用户说“把刚才那个报告发到微信/QQ/飞书”
  -> resolve_resource
  -> validate_channel_capability
  -> send_or_upload
  -> retry_or_fallback
  -> verify_delivery
  -> give_up_explained
```

## States

`resolve_resource`

- Uses `ResourceResolver`.
- May produce resolved, ambiguous, not_found, or blocked.

`ask_user`

- Used for ambiguous or missing resources.
- The action is either `confirm_resource` or `clarify_resource`.

`fallback`

- Used when the target channel cannot send the attachment type.
- Selects a declared `ChannelCapability.fallback_actions` entry.

`send_or_upload`

- Used when resource and channel capability are compatible.

`retry`

- Used when delivery fails but retry budget remains.
- Current action is `retry_upload`.

`verify_result`

- Used after successful delivery.
- The action is `verify_delivery`.

`give_up_explained`

- Used for blocked resources or exhausted retry budget.
- Must include a user-visible reason.

## Implementation

The initial state machine lives in `src/hypo_agent/core/active_recovery.py`.

It consumes:

- `ResourceResolution`
- `ChannelCapability`
- `DeliveryResult`

It returns `ActiveRecoveryDecision`:

- `state`
- `action`
- `recovery_action`
- `retry_after_attempts`

## Boundaries

The state machine does not call tools, send messages, or mutate pipeline state. It is a decision core that pipeline/channel orchestration can call.

This keeps active recovery testable and avoids spreading retry/fallback logic across individual channel adapters.

## Follow-Up

- Integrate decisions into pipeline/channel orchestration.
- Add resume tokens so a user confirmation can continue the original operation.
- Add per-channel verification where APIs expose message ids or upload ids.
- Add opt-in real channel smoke tests.
