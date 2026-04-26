# Changelog

## v1.5.0-m15-r7 - 2026-04-26

### Highlights

- Added the M15 channel and transport refactor work since `v1.4.0-m14`, including unified message delivery, Feishu/QQ/Weixin channel updates, dashboard refinements, heartbeat/Notion integration, and runtime config hardening.
- Added skill runtime improvements: execution profiles, skill catalog migration, contract/acceptance gates, Notion schema validation, and test-mode probe reporting.
- Added Codex-backed repair and isolated Codex job lane support with persisted job status, trace IDs, abort handling, and WebUI status surfaces.
- Added tool outcome taxonomy, trace schema persistence, and weighted circuit-breaker accounting so recoverable model/user/policy errors do not incorrectly fuse tools.
- Added typed memory, automatic migration, async consolidation, backup/report/rollback support, and prompt-safe memory injection.
- Added the non-blocking message runtime with per-session ordering, cross-session concurrency, work status events, cancellation, timeout handling, and feature-flagged rollout.
- Improved WebUI long-session performance with streaming chunk buffering, markdown render caching/defer behavior, long-history pagination, Codex job cards, and typed semantic memory management.
- Added audit and implementation reports for M1-M4 and R1-R7.

### Validation

- `uv run pytest tests/security/test_circuit_breaker.py tests/skills/test_skill_manager.py tests/memory/test_structured_store.py tests/core/test_skill_verification.py tests/skills/test_notion_skill.py tests/skills/test_skill_contracts.py tests/channels/test_codex_bridge.py tests/core/test_codex_job_service.py tests/memory/test_codex_job_store.py tests/core/test_pipeline.py tests/gateway/test_memory_api.py tests/memory/test_typed_memory_migration.py tests/skills/test_memory_skill.py tests/core/test_config_loader.py tests/gateway/test_app_scheduler_lifecycle.py tests/memory/test_memory_consolidation.py tests/memory/test_memory_gc.py tests/core/test_pipeline_event_consumer.py -q`: 196 passed.
- `cd web && npm run test`: 108 passed.
- `cd web && npm run build`: passed.
- `uv run python -m py_compile ...`: passed for touched backend modules.
- `git diff --check`: passed.
- Production health after restart: `runtime_mode=prod`, event consumer and scheduler available on `127.0.0.1:8765`.

### Notes

- `bash test_run.sh` is a long-running test-mode server start command, not a self-terminating test suite.
- Default `HYPO_TEST_MODE=1 ... agent_cli.py --port 8766 smoke` was blocked by its production-listener guard because the requested production instance remained active on `127.0.0.1:8765`; production was not stopped to force the default smoke.
