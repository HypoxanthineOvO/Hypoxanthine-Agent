# R1 Outcome Taxonomy, Trace Schema, And Breaker Accounting

Date: 2026-04-26

## Summary

R1 implemented structured tool outcome classification, persisted trace metadata, and switched circuit-breaker accounting from generic failures to weighted outcomes.

The main behavior change is that model mistakes, missing user input, and policy blocks are observable but do not consume breaker budget. External outages and real tool bugs still count, with explicit `breaker_weight`.

## Changes

- Added `src/hypo_agent/core/tool_outcome.py` with outcome classes, retryability, breaker weight, side-effect class, operation, and Chinese user-visible summaries.
- Extended `SkillManager` to attach outcome metadata and `trace_id` to `SkillOutput.metadata` and persisted tool invocation rows.
- Added `CircuitBreaker.record_outcome(...)` and kept `record_failure(...)` as a compatibility wrapper.
- Reset session-level failure count on `record_success(...)`, so a failed attempt followed by success does not leave hidden session breaker debt.
- Extended `tool_invocations` with `outcome_class`, `retryable`, `breaker_weight`, `side_effect_class`, `operation`, `trace_id`, and `user_visible_summary`.
- Added migration logic for legacy SQLite databases that only have the old `tool_invocations` columns.
- Added `docs/architecture/outcome-taxonomy-trace-schema.md` documenting taxonomy and schema compatibility.

## Tests

RED phase confirmed expected failures for missing `record_outcome`, missing trace columns, and missing SkillManager outcome metadata.

GREEN / regression commands:

```bash
uv run pytest tests/security/test_circuit_breaker.py tests/memory/test_structured_store.py tests/skills/test_skill_manager.py -q
uv run pytest tests/skills/test_skill_manager.py tests/core/test_pipeline_tools.py tests/security/test_permission_manager.py -q
uv run pytest tests/skills/test_exec_skill.py tests/core/test_progressive_disclosure.py -q
uv run pytest tests/security/test_circuit_breaker.py tests/memory/test_structured_store.py -q
uv run pytest tests/gateway/test_sessions_api.py tests/gateway/test_dashboard_api.py tests/skills/test_log_inspector_skill.py -q
uv run pytest tests/security/test_circuit_breaker.py tests/memory/test_structured_store.py tests/skills/test_skill_manager.py tests/core/test_pipeline_tools.py tests/security/test_permission_manager.py tests/skills/test_exec_skill.py tests/core/test_progressive_disclosure.py -q
```

Observed results:

- Focused SkillManager / StructuredStore / CircuitBreaker suite: 47 passed before the session-reset review fix, then the breaker suite passed with 10 tests.
- Combined R1 prompt and regression suite: 129 passed.
- Existing warnings are from `lark_oapi` and `websockets` deprecations.

Default smoke was attempted with the project test-mode command sequence. It was not executed because `scripts/agent_cli.py` detected an existing listener on production port `8765` while `HYPO_TEST_MODE=1` was active and refused to mix test smoke with the deployed instance.

## Evaluation

| Metric | Score | Notes |
| --- | ---: | --- |
| diff_score | 1 | Scoped to SkillManager, CircuitBreaker, StructuredStore, tests, and architecture note |
| code_quality | 4 | Clear behavior split; some duplicated operation inference remains acceptable for R1 |
| test_coverage | 2 | Focused regression coverage added; full smoke blocked by production-listener safety |
| complexity | 3 | Adds taxonomy and persistence fields without changing public `status` semantics |
| architecture_drift | 1 | Aligns with roadmap and keeps legacy readers compatible |

## Residual Risk

- Outcome classification is intentionally heuristic in R1. R2 should move skill-specific expected errors into explicit contracts and probes so classification can become less string-based.
- `dangerous_failure` is reserved but not yet emitted by a concrete path.
- Default smoke should be rerun after the production listener on `8765` is intentionally stopped or isolated.
