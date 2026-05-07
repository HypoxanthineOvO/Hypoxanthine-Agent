# Changelog

## v1.7.0 - 2026-05-07

### Highlights

- Improved Agent tool-call UX so recoverable intermediate tool/model failures are folded internally and only terminal failures produce concise user-facing summaries.
- Collapsed repeated fallback progress messages into a stable single running state while preserving final success/failure replay.
- Added retry for transient search/web-read timeouts and kept successful model fallback notices out of external channels.
- Enabled the non-blocking message runtime by default so inbound channel messages enqueue quickly while long tasks run in the background.
- Refreshed Hypo-Workflow/OpenCode adapters to version `12.1.0`, adding `/hw:pr` and `/hw:explain` mappings and removing the retired dashboard command.

### Validation

- `uv run pytest -q`: passed.
- `cd web && npm test -- --run`: passed.
- `cd web && npm run build`: passed.
- `hypo-workflow sync --repair --platform opencode --project /home/heyx/Hypo-Agent`: completed with `errors:0`; one non-blocking warning remains for missing `.pipeline/reports.compact.md` source discovery.
- Production health after restart: backend `http://127.0.0.1:8765/api/health` returned ok and frontend `http://127.0.0.1:5178` returned `200`.

## v1.6.0 - 2026-05-05

### Highlights

- Added robust agent recovery for tool calls and model fallback, including collapsed retryable tool states, user-facing final failure summaries, and clearer tool display names.
- Added QQ/Weixin/Feishu outbound delivery capability handling, QQ Bot inbound text+image attachment parsing, and the `hypo-agent send` outbound CLI/API path.
- Added the GPT-Image-2 image generation skill with generation/editing tools, channel intent handling, history tracking, and audit coverage.
- Added a dedicated Notion Plan skill for HYX 计划通, including structure discovery, academic semester month-year inference, item parsing, dry-run previews, idempotent insertion, and corrected duplicate/month targeting.
- Added OpenCode/Hypo-Workflow adapter artifacts, architecture notes, acceptance runbooks, and release batching reports.

### Validation

- `uv run pytest -q`: passed.
- `cd web && npm test -- --run`: 21 files / 110 tests passed.
- `cd web && npm run build`: passed.
- `hypo-workflow sync --repair`: completed with `errors:0`; one non-blocking derived warning remains for `.pipeline/reports.compact.md` source discovery.
- Production health after restart: backend `http://127.0.0.1:8765` and frontend `http://127.0.0.1:5178` returned `200`.

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
