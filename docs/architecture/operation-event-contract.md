# Operation Event Contract

Date: 2026-05-03
Scope: C1-M7

## Goal

Operation events provide one envelope for backend, channels, and WebUI to observe recovery work.

The primary consumer is not WebUI. The contract must be usable by QQ/微信/飞书 channel messages as well as future UI surfaces.

## Envelope

```json
{
  "type": "operation_event",
  "event_type": "resource_candidates",
  "operation_id": "op-1",
  "session_id": "main",
  "status": "needs_confirmation",
  "timestamp": "...",
  "candidates": [],
  "delivery": {},
  "recovery_action": {}
}
```

## Event Types

`resource_candidates`

- Resource resolver found candidates.
- Usually paired with `recovery_action.type=ask_user`.

`channel_delivery`

- Channel send/upload outcome.
- Includes channel, status, delivery payload, and optional fallback action.

`recovery_action`

- Reserved for generic recovery state updates.

`verify_result`

- Reserved for successful delivery verification.

## Current Implementation

`src/hypo_agent/core/operation_events.py` defines `OperationEvent`.

The current implementation serializes:

- resource candidate confirmation events
- channel delivery result events

It does not yet wire those events into WebSocket or channel outbound messages.

## Follow-Up

M8 should validate the contract with layered tests.

Later orchestration should emit `OperationEvent` when:

- resource resolution is ambiguous;
- tool validation returns recovery action;
- active recovery chooses fallback;
- delivery succeeds or fails;
- verification completes.
