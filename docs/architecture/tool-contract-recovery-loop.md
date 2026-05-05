# Tool Contract Recovery Loop

Date: 2026-05-03
Scope: C1-M3

## Goal

Tool failures should produce structured recovery data instead of final low-quality errors.

M3 establishes the first shared recovery envelope used by tools, `SkillManager`, `ToolOutcome`, and later active recovery logic.

## Current Contract

Recoverable tool outputs can include:

```json
{
  "missing_fields": ["path"],
  "recovery_action": {
    "type": "ask_user",
    "reason": "missing_required_arguments",
    "message": "缺少必要参数：path。请补充后重试。"
  }
}
```

Resource failures from M2 can include:

```json
{
  "resource_resolution": {"status": "ambiguous"},
  "resource_candidates": [
    {
      "kind": "file",
      "uri": "/path/channel-report.md",
      "display_name": "channel-report.md",
      "source": "search_root"
    }
  ],
  "recovery_action": {
    "type": "ask_user",
    "reason": "multiple_candidates",
    "message": "找到多个候选资源，请确认要使用哪一个。"
  }
}
```

`ToolOutcome` treats errors with explicit `recovery_action` as:

- `outcome_class=user_input_error`
- `retryable=true`
- `breaker_weight=0`

This is intentional. Missing input and ambiguous resources should not poison the circuit breaker.

## Implemented In M3

`SkillManager.invoke` now validates JSON Schema `required` fields before calling a skill.

If a required argument is missing:

- the skill is not executed;
- `SkillOutput.status` is `error`;
- `metadata.missing_fields` is populated;
- `metadata.recovery_action` asks for the missing input;
- outcome metadata is attached as recoverable user input error.

This is a narrow first step. It does not yet implement full JSON Schema validation for type, enum, min/max, nested objects, or oneOf/anyOf.

## Channel-First Implication

For channel delivery, the recovery loop should eventually handle:

```text
missing target channel
missing resource
ambiguous resource
channel cannot send this attachment type
upload failed
delivery result uncertain
```

M3 only establishes the envelope shape and required-field guard. M5 should extend it with channel capability and delivery fallback actions.

## Remaining Work

- Add full parameter validator or a controlled JSON Schema subset.
- Teach pipeline to continue from recovery actions instead of only surfacing metadata.
- Add operation-level resume tokens.
- Add channel-specific actions such as `fallback_to_link`, `send_summary`, and `retry_upload`.
- Add non-WebUI confirmation UX for QQ/微信/飞书.
