# Repair Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current one-shot `/repair` behavior with a first-class repair workflow backed by an in-process `CodexBridge`, with reporting, tracked repair runs, structured reports, retry, and guarded auto-restart.

**Architecture:** Add a dedicated `CodexBridge` and move repair execution off Hypo-Coder HTTP/webhook completely. `RepairService` will orchestrate repair runs, persist workflow state in SQLite, and receive in-process completion callbacks from `CodexBridge`. `/codex` stays on the old backend for now.

**Tech Stack:** Python, FastAPI, aiosqlite, Codex Python SDK (`codex_app_server`), existing `SlashCommandHandler`, `SessionMemory`, pytest

---

### Task 1: Save the approved design and add the implementation plan

**Files:**
- Create: `docs/plans/2026-04-22-repair-workflow-design.md`
- Create: `docs/plans/2026-04-22-repair-workflow-implementation-plan.md`

**Step 1: Save the design doc**

Write the approved repair workflow design with:
- command surface
- state model
- prompt contract
- restart safety rules
- known pattern detection

**Step 2: Save the implementation plan**

Write this plan with exact files, tests, and verification commands.

### Task 2: Add repair workflow persistence with tests first

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Create: `tests/memory/test_repair_store.py`

**Step 1: Write failing tests**

Cover:
- create repair run
- lookup by `run_id`
- active run lookup
- latest run by session
- append and list run events
- update run status / task binding / report fields
- retry linkage

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/memory/test_repair_store.py -q`
Expected: FAIL because repair tables and APIs do not exist yet.

**Step 3: Implement the minimal store APIs**

Add:
- `repair_runs`
- `repair_run_events`
- CRUD / list / update helpers

**Step 4: Run the tests again**

Run: `uv run pytest tests/memory/test_repair_store.py -q`
Expected: PASS

### Task 3: Add `CodexBridge`

**Files:**
- Create: `src/hypo_agent/channels/codex_bridge.py`
- Create: `tests/channels/test_codex_bridge.py`

**Step 1: Write failing bridge tests**

Cover:
- start / stop lifecycle
- submit success callback
- submit failure callback
- native continuation via `thread_resume`
- abort
- in-memory status lookup

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/channels/test_codex_bridge.py -q`
Expected: FAIL because `CodexBridge` does not exist.

**Step 3: Implement the minimal bridge**

Include:
- `AsyncCodex` startup / shutdown
- `thread_start`
- `thread_resume`
- async background execution
- callback on completion / failure / abort

**Step 4: Run the tests again**

Run: `uv run pytest tests/channels/test_codex_bridge.py -q`
Expected: PASS

### Task 4: Migrate `RepairService` onto `CodexBridge`

**Files:**
- Modify: `src/hypo_agent/core/repair_service.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `tests/core/test_repair_service.py`
- Modify: `tests/memory/test_repair_store.py`

**Step 1: Write failing service/store tests**

Cover:
- `codex_thread_id` persistence
- `RepairService.start_run` uses `CodexBridge.submit`
- immediate submit failure does not leave run stuck
- retry prefers native continuation
- in-process completion callback updates run state
- JSON parse failure downgrades to `needs_review`
- restart recovery on startup

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/memory/test_repair_store.py tests/core/test_repair_service.py -q`
Expected: FAIL because repair still depends on the old backend.

**Step 3: Implement the migration**

- add `codex_thread_id`
- replace `coder_task_service` with `codex_bridge`
- add in-process `_on_repair_complete`
- add recovery logic

**Step 4: Run the tests again**

Run: `uv run pytest tests/memory/test_repair_store.py tests/core/test_repair_service.py -q`
Expected: PASS

### Task 5: Keep `/repair` slash commands stable while swapping backend

**Files:**
- Modify: `src/hypo_agent/core/slash_commands.py`
- Modify: `tests/core/test_slash_commands.py`

**Step 1: Write failing slash tests**

Cover:
- `/repair report`
- `/repair do`
- `/repair status`
- `/repair logs`
- `/repair abort`
- `/repair retry`

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/core/test_slash_commands.py -q`
Expected: FAIL if slash command behavior drifts during backend swap.

**Step 3: Keep slash behavior stable**

Do not change user-facing repair command semantics; only the backend changes.

**Step 4: Run the tests again**

Run: `uv run pytest tests/core/test_slash_commands.py -q`
Expected: PASS

### Task 6: Wire `CodexBridge` and `RepairService` through app startup

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/gateway/test_app_test_mode.py`

**Step 1: Write failing wiring tests**

Cover:
- app builds `CodexBridge`
- app builds `RepairService`
- slash commands receive it
- reload path rebuilds and reattaches it
- lifespan starts/stops bridge
- restart recovery runs at startup

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/gateway/test_app_test_mode.py -q`
Expected: FAIL because repair service is not present in app deps/state.

**Step 3: Implement the wiring**

Inject:
- `repair_service`
- proactive callback
- restart handler
- repo root

**Step 4: Run the tests again**

Run: `uv run pytest tests/gateway/test_app_test_mode.py -q`
Expected: PASS

### Task 7: Remove repair dependence on Hypo-Coder webhook/watcher

**Files:**
- Modify: `src/hypo_agent/channels/coder/coder_webhook.py`
- Modify: `src/hypo_agent/channels/coder/coder_stream_watcher.py`
- Modify: `tests/channels/test_coder_webhook.py`
- Modify: `tests/channels/test_coder_stream_watcher.py`

**Step 1: Update tests**

Remove repair-specific webhook/watcher expectations and keep `/codex`-related behavior intact.

**Step 2: Run the tests**

Run: `uv run pytest tests/channels/test_coder_webhook.py tests/channels/test_coder_stream_watcher.py -q`
Expected: PASS

### Task 8: Add targeted regression coverage for the Genesis QWen sample

**Files:**
- Modify: `tests/core/test_repair_service.py`
- Modify: `tests/core/test_slash_commands.py`

**Step 1: Add the failing regression tests**

Cover:
- report shows the known pattern finding
- `/repair do --from <finding_id>` carries the Genesis QWen issue text into the prompt

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/core/test_repair_service.py tests/core/test_slash_commands.py -q`
Expected: FAIL until the detector and prompt builder are wired.

**Step 3: Implement the minimal detector/prompt behavior**

Do not fix the model bug itself. Only make it detectable and actionable through repair.

**Step 4: Run the tests again**

Run: `uv run pytest tests/core/test_repair_service.py tests/core/test_slash_commands.py -q`
Expected: PASS

### Task 9: Final verification

**Files:**
- Check: `src/hypo_agent/core/repair_service.py`
- Check: `src/hypo_agent/channels/codex_bridge.py`
- Check: `src/hypo_agent/memory/structured_store.py`
- Check: `src/hypo_agent/core/slash_commands.py`
- Check: `src/hypo_agent/channels/coder/coder_stream_watcher.py`
- Check: `src/hypo_agent/channels/coder/coder_webhook.py`
- Check: `src/hypo_agent/gateway/app.py`

**Step 1: Run targeted tests**

Run:
- `uv run pytest tests/channels/test_codex_bridge.py -q`
- `uv run pytest tests/memory/test_repair_store.py tests/core/test_repair_service.py tests/core/test_slash_commands.py -q`
- `uv run pytest tests/channels/test_coder_stream_watcher.py tests/channels/test_coder_webhook.py -q`

Expected: PASS

**Step 2: Run broader regression**

Run:
- `uv run pytest tests/core/test_pipeline_tools.py tests/core/test_self_restart.py tests/skills/test_coder_task_service.py tests/test_models_serialization.py -q`

Expected: PASS

**Step 3: Summarize completion**

Report:
- changed files
- repair workflow delivered
- verification commands run
- any residual limits
