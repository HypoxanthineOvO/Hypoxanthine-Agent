# R3 Codex Isolated Execution Lane

Date: 2026-04-26

## Summary

R3 added the first opt-in Codex job lane: app-server isolation parameters, structured `codex_jobs` persistence, transient progress delivery, abort handling, and a small service wrapper for high-level Codex operations.

This does not yet replace all existing `/codex`, repair, or Hypo-Coder paths. It establishes the backend model those paths can migrate toward.

## Changes

- Extended `CodexBridge` with:
  - `codex_home`
  - `app_server_cwd`
  - `config_overrides`
  - `isolation_mode`
  - `AppServerConfig(env/cwd/config_overrides)` construction
- Added `StructuredStore` tables and methods for:
  - `codex_jobs`
  - `codex_job_events`
- Added `src/hypo_agent/core/codex_job_service.py` with:
  - `submit_job(...)`
  - `abort_job(...)`
  - structured event persistence
  - transient `tool_status` progress pushes
  - explicit no-L1 persistence behavior
- Added architecture documentation in `docs/architecture/codex-isolated-execution-lane.md`.

## Tests

RED phase:

- Missing `CodexJobService`.
- Missing `codex_jobs` store methods.
- Missing CodexBridge isolation `AppServerConfig` parameters.

GREEN / regression commands:

```bash
uv run pytest tests/channels/test_codex_bridge.py::test_codex_bridge_start_passes_isolated_home_env_and_config tests/memory/test_codex_job_store.py tests/core/test_codex_job_service.py -q
uv run pytest tests/channels/test_codex_bridge.py tests/core/test_repair_service.py tests/memory/test_coder_task_store.py -q
uv run pytest tests/channels/test_codex_bridge.py tests/core/test_repair_service.py tests/memory/test_coder_task_store.py tests/memory/test_codex_job_store.py tests/core/test_codex_job_service.py tests/core/test_slash_commands.py -q
python -m py_compile src/hypo_agent/channels/codex_bridge.py src/hypo_agent/core/codex_job_service.py src/hypo_agent/memory/structured_store.py
git diff --check
```

Observed results:

- Focused R3 isolation/job tests: 4 passed.
- Prompt-suggested Codex/repair/coder regression: 20 passed.
- Combined R3 related regression: 59 passed.
- Existing warnings are from `lark_oapi` and `websockets` deprecations.

## Evaluation

| Metric | Score | Notes |
| --- | ---: | --- |
| diff_score | 2 | Adds new job service/store tables and CodexBridge isolation parameters |
| code_quality | 4 | Isolated opt-in service; old bridge/repair contracts preserved |
| test_coverage | 2 | Covers isolation config, job persistence, transient progress, abort, and regressions |
| complexity | 3 | Adds a new job lane but keeps it thin |
| architecture_drift | 1 | Matches roadmap and explicitly preserves old paths |

## Residual Risk

- External Codex history isolation still depends on SDK/CLI honoring `CODEX_HOME`; R3 documents this limit.
- The new job service is not yet fully wired into slash commands, repair, or Hypo-Coder submission paths.
- `codex_jobs` and `repair_runs` coexist for now; unification should be a follow-up after compatibility endpoints are planned.
