# Channel File Delivery

Date: 2026-05-03
Scope: C1-M5

## Goal

QQ/微信/飞书 outbound delivery should expose a shared attachment capability contract.

The priority path is:

```text
ResourceRef
  -> target channel
  -> ChannelCapability
  -> send/upload attachment
  -> DeliveryResult with per-attachment outcomes
  -> recovery action when delivery cannot complete
```

## Core Types

`ChannelCapability`

- `channel`
- `supports_text`
- `supported_attachment_types`
- `max_attachment_bytes`
- `fallback_actions`

`AttachmentDeliveryOutcome`

- `filename`
- `attachment_type`
- `success`
- `error`
- `recovery_action`

`DeliveryResult`

- still reports channel-level success, segment counts, and error
- now also carries `attachment_outcomes`

## Implemented Capability Declarations

QQ Bot:

- channel: `qq_bot`
- attachment types: image, file, audio, video
- fallback: `fallback_to_link`, `send_summary`

Weixin:

- channel: `weixin`
- attachment types: image, file, video
- fallback: `send_summary`, `fallback_to_link`

Feishu:

- channel: `feishu`
- attachment types: image, file
- fallback: `fallback_to_link`, `send_summary`

## Current Limits

M5 creates the shared contract and exposes capability declarations. It does not yet rewrite every channel send branch to emit detailed per-attachment outcomes on each real upload path.

Existing channel tests already verify payload construction and simulated sends for key file/image paths. The new M5 contract makes those paths observable by future active recovery logic.

## Required Follow-Up

M6 should use capability data before sending:

```text
if not capability.supports_attachment_type(ref.type):
  return recovery_action=fallback_to_link/send_summary
```

Channel adapters should gradually add per-attachment outcome recording around each upload/send operation.

Real smoke tests should remain opt-in because QQ/微信/飞书 delivery depends on configured accounts, tokens, and reachable test recipients.
