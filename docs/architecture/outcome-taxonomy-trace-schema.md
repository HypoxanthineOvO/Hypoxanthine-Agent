# Outcome Taxonomy And Tool Trace Schema

Date: 2026-04-26

## Purpose

Tool failures are no longer treated as one generic failure class. Hypo-Agent now classifies tool outcomes before circuit-breaker accounting so model mistakes, user input errors, permission blocks, external outages, and tool bugs do not all burn the same failure budget.

## Outcome Classes

| Class | Meaning | Retryable | Breaker Weight |
| --- | --- | --- | --- |
| `success` | Tool completed successfully | no | 0 |
| `model_error` | Model selected an unknown tool or malformed invocation | no | 0 |
| `user_input_error` | Missing required input or target does not exist | no | 0 |
| `policy_block` | Permission policy or kill switch blocked execution | no | 0 |
| `external_unavailable` | External service timeout or temporary outage | yes | 1 |
| `tool_bug` | Tool raised an internal error or returned invalid output | no | 1 |
| `dangerous_failure` | Reserved for security-sensitive failures | no | strong failure weight |

Only weighted outcomes affect breaker counters. Zero-weight outcomes are still persisted for observability but do not fuse tools or sessions.

## Tool Trace Fields

`tool_invocations` keeps its legacy `status` column for compatibility and adds trace-oriented columns:

- `outcome_class`
- `retryable`
- `breaker_weight`
- `side_effect_class`
- `operation`
- `trace_id`
- `user_visible_summary`

These fields are also attached to `SkillOutput.metadata` so the caller can render concise user-facing summaries without parsing raw error text.

## Compatibility

Existing readers can continue using `status`, `result_summary`, `error_info`, and `compressed_meta_json`. New code should prefer `outcome_class` and `user_visible_summary` for diagnosis and UI summaries.

The circuit breaker still exposes `record_failure` and `record_success`; `record_failure` delegates to weighted outcome accounting with a default weight of `1`. New call sites should use `record_outcome` through `SkillManager` where possible.
