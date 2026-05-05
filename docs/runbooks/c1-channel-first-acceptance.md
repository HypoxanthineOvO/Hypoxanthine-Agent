# C1 Channel-First Acceptance Matrix

Date: 2026-05-03

## Default Local Gates

These tests must run without external side effects:

```bash
uv run pytest \
  tests/core/test_resource_resolution.py \
  tests/core/test_tool_outcome.py \
  tests/core/test_delivery_capability.py \
  tests/core/test_active_recovery.py \
  tests/core/test_operation_events.py \
  tests/skills/test_skill_manager.py \
  tests/skills/test_fs_skill.py \
  tests/channels/test_channel_attachment_capabilities.py \
  tests/channels/test_feishu_channel.py \
  tests/channels/test_weixin_adapter.py \
  tests/gateway/test_qqbot_channel.py \
  -q
```

Expected coverage:

- resource resolution
- tool recovery metadata
- required argument validation
- channel attachment capability
- active recovery decisions
- operation event payloads
- simulated QQ/微信/飞书 file/image payload paths

## Broader Local Regression

Run when preparing a branch for merge:

```bash
uv run pytest tests/core tests/skills tests/channels tests/gateway -q
```

This may be slower but should still avoid real external sends.

## Optional Real Channel Smoke

These are opt-in only. Do not run them unless the operator explicitly provides test accounts/tokens and confirms external sending.

Required configuration:

- QQ Bot: app id, app secret, target openid, public file URL base.
- Weixin: bot token, target user id, working iLink client.
- Feishu: app id, app secret, target chat id.

Required behavior:

- send a small markdown file;
- send a small image;
- record message id/upload id when available;
- record skip reason if any required credential is missing.

## Deferred Web/Browser Gates

M4 webpage reading was deferred because the user prioritized channels.

Before release, run web/browser gates only if the release scope includes webpage reading:

- real URL extraction;
- Zhihu logged-in page;
- browser-rendered fallback;
- cookie/session reuse.

## Release Risk Checklist

- Real channel sends not executed: release remains local-contract validated only.
- OperationEvent not wired into outbound channel messages: active recovery is observable at contract level, not end-to-end UI/channel level.
- Pipeline does not yet persist resume tokens for user confirmation.
- Per-upload attachment outcomes are partially modeled but not fully emitted by every channel branch.
