# Codex Slash Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a session-aware `/codex` command family backed by Hypo-Coder, including task persistence, attach/detach semantics, webhook routing, degraded realtime updates, and reply status bars.

**Architecture:** Keep `/codex` as a zero-token slash-command path. Introduce a dedicated `CoderTaskService` shared by slash commands, the existing `CoderSkill`, and webhook routing. Persist task/session bindings in SQLite so task ownership, working-directory reuse, attach state, and status-bar rendering all use one source of truth. Because the current Hypo-Coder client exposes only create/get/list/abort/health, implement capability probes and degrade `send` plus realtime streaming cleanly until the remote API grows continuation or event-stream endpoints.

**Tech Stack:** Python, FastAPI, aiosqlite, existing `ChatPipeline`, `SlashCommandHandler`, `CoderClient`, pytest

---

### Task 1: Save the approved plan and confirm runtime constraints

**Files:**
- Create: `docs/plans/2026-04-05-codex-slash-command-implementation-plan.md`
- Check: `src/hypo_agent/channels/coder/coder_client.py`
- Check: `src/hypo_agent/channels/coder/coder_webhook.py`

**Step 1: Save the plan**

Write the approved implementation plan to this file.

**Step 2: Confirm command family**

Support:
- `/codex <prompt> --dir /path`
- `/codex send <instruction>`
- `/codex status <task_id|last>`
- `/codex list [status]`
- `/codex abort <task_id|last>`
- `/codex done`
- `/codex attach <task_id>`
- `/codex detach`
- `/codex health`

**Step 3: Confirm working-directory fallback**

Priority:
1. Explicit `--dir`
2. Current session latest `coder_tasks.working_directory`
3. `/home/heyx/Hypo-Agent`

**Step 4: Confirm current Hypo-Coder limits**

Current client only supports:
- `create_task`
- `get_task`
- `list_tasks`
- `abort_task`
- `health`

Degraded behavior for now:
- `supports_streaming()` returns `False`
- `supports_continuation()` returns `False`
- `/codex send` returns a clear unsupported message
- realtime updates use lightweight status polling/watcher scaffolding only

### Task 2: Add coder task persistence with tests first

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Create: `tests/memory/test_coder_task_store.py`

**Step 1: Write failing tests**

Cover:
- create task mapping
- lookup by `task_id`
- latest task by `session_id`
- list tasks by session
- attach and detach updates
- `done` ends the current session binding
- status updates persist

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/memory/test_coder_task_store.py -q`
Expected: FAIL because the store APIs and table do not exist yet.

**Step 3: Implement the minimal store APIs**

Add a `coder_tasks` table and methods for:
- create
- get by task
- get latest by session
- list by session/status
- attach
- detach
- mark done
- update status

**Step 4: Run the tests again**

Run: `uv run pytest tests/memory/test_coder_task_store.py -q`
Expected: PASS

### Task 3: Extract a dedicated CoderTaskService

**Files:**
- Create: `src/hypo_agent/channels/coder/coder_task_service.py`
- Modify: `src/hypo_agent/skills/coder_skill.py`
- Create: `tests/skills/test_coder_task_service.py`
- Modify: `tests/skills/test_coder_skill.py`

**Step 1: Write failing service tests**

Cover:
- submit persists task and attaches the session
- default working-directory resolution
- explicit `--dir` wins
- `status last`
- `abort last`
- `attach`
- `detach`
- `done`
- `send` returns unsupported when continuation is unavailable
- capability probes report `False`

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/skills/test_coder_task_service.py tests/skills/test_coder_skill.py -q`
Expected: FAIL because the service does not exist yet.

**Step 3: Implement minimal service behavior**

Move task orchestration out of `CoderSkill` into `CoderTaskService`.

**Step 4: Make `CoderSkill` delegate to the service**

Keep the existing tool names and result style stable where possible.

**Step 5: Run the tests again**

Run: `uv run pytest tests/skills/test_coder_task_service.py tests/skills/test_coder_skill.py -q`
Expected: PASS

### Task 4: Add client capability probes for continuation and streaming

**Files:**
- Modify: `src/hypo_agent/channels/coder/coder_client.py`
- Create: `tests/channels/test_coder_client.py`

**Step 1: Write failing tests**

Cover:
- existing client calls still work
- `supports_streaming()` returns `False`
- `supports_continuation()` returns `False`

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/channels/test_coder_client.py -q`
Expected: FAIL because the probe methods do not exist yet.

**Step 3: Implement minimal probe methods**

Do not invent unsupported endpoints. Return explicit capability booleans only.

**Step 4: Run the tests again**

Run: `uv run pytest tests/channels/test_coder_client.py -q`
Expected: PASS

### Task 5: Add `/codex` slash command family

**Files:**
- Modify: `src/hypo_agent/core/slash_commands.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/core/test_slash_commands.py`

**Step 1: Write failing slash tests**

Cover:
- `/codex <prompt>`
- `/codex <prompt> --dir /tmp/repo`
- `/codex send ...`
- `/codex status last`
- `/codex list`
- `/codex abort last`
- `/codex attach <task_id>`
- `/codex detach`
- `/codex done`
- `/codex health`
- invalid or missing arguments

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/core/test_slash_commands.py -q`
Expected: FAIL because `/codex` is not registered.

**Step 3: Implement the command family**

Add a dedicated `/codex` dispatcher inside `SlashCommandHandler`.

**Step 4: Wire the service into app startup**

Construct the service in gateway dependencies and pass it into slash commands.

**Step 5: Run the tests again**

Run: `uv run pytest tests/core/test_slash_commands.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py -q`
Expected: PASS

### Task 6: Route coder webhooks to the owning session

**Files:**
- Modify: `src/hypo_agent/channels/coder/coder_webhook.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/channels/test_coder_webhook.py`

**Step 1: Write failing webhook tests**

Cover:
- completed routes to the mapped session
- failed routes to the mapped session
- task status updates persist
- detached tasks do not emit chat pushes
- unknown tasks return OK without crashing

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/channels/test_coder_webhook.py -q`
Expected: FAIL because webhook currently hardcodes `session_id="main"`.

**Step 3: Implement session-aware routing**

Resolve `task_id` through `coder_tasks`, update task status, then conditionally push based on attach state.

**Step 4: Run the tests again**

Run: `uv run pytest tests/channels/test_coder_webhook.py -q`
Expected: PASS

### Task 7: Add degraded realtime watcher scaffolding

**Files:**
- Create: `src/hypo_agent/channels/coder/coder_stream_watcher.py`
- Modify: `src/hypo_agent/channels/coder/coder_task_service.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Create: `tests/channels/test_coder_stream_watcher.py`

**Step 1: Write failing watcher tests**

Cover:
- attached running task starts one watcher
- duplicate starts are ignored
- watcher polls status and pushes bounded updates
- detached tasks stop emitting messages
- finished tasks stop the watcher

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/channels/test_coder_stream_watcher.py -q`
Expected: FAIL because the watcher does not exist yet.

**Step 3: Implement minimal degraded watcher**

Because streaming is unsupported:
- poll task status
- emit only meaningful status transitions or bounded progress notices
- keep output aggregation simple with a single size threshold near 800 chars

**Step 4: Run the tests again**

Run: `uv run pytest tests/channels/test_coder_stream_watcher.py -q`
Expected: PASS

### Task 8: Inject Codex status bars into normal assistant replies

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `tests/core/test_pipeline.py`

**Step 1: Write failing pipeline tests**

Cover:
- attached task appends status bar
- detached or done task does not append
- `/codex` replies do not get the status bar

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/core/test_pipeline.py -q`
Expected: FAIL because pipeline does not know about attached coder tasks.

**Step 3: Implement minimal status-bar injection**

Append at final text render time using current attached task info from the store or service.

**Step 4: Run the tests again**

Run: `uv run pytest tests/core/test_pipeline.py -q`
Expected: PASS

### Task 9: Expose session coder-task inspection APIs

**Files:**
- Modify: `src/hypo_agent/gateway/sessions_api.py`
- Create or Modify: `tests/gateway/test_sessions_api.py`

**Step 1: Write failing API tests**

Cover:
- current attached task for a session
- historical coder tasks for a session

**Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/gateway/test_sessions_api.py -q`
Expected: FAIL because the API does not exist yet.

**Step 3: Implement minimal endpoints**

Add session-scoped coder-task read APIs without changing the existing message/tool-invocation endpoints.

**Step 4: Run the tests again**

Run: `uv run pytest tests/gateway/test_sessions_api.py -q`
Expected: PASS

### Task 10: Update docs and run final verification

**Files:**
- Modify: `docs/architecture.md`
- Create: `docs/runbooks/2026-04-05-codex-slash-command.md`

**Step 1: Update architecture docs**

Document:
- `/codex` slash entry
- `CoderTaskService`
- `coder_tasks`
- degraded watcher plus webhook split
- working-directory fallback

**Step 2: Add a runbook**

Document command syntax, attach semantics, unsupported `send`, and degraded realtime behavior.

**Step 3: Run final verification**

Run:

```bash
uv run pytest \
  tests/memory/test_coder_task_store.py \
  tests/skills/test_coder_task_service.py \
  tests/skills/test_coder_skill.py \
  tests/channels/test_coder_client.py \
  tests/channels/test_coder_webhook.py \
  tests/channels/test_coder_stream_watcher.py \
  tests/core/test_slash_commands.py \
  tests/core/test_pipeline.py \
  tests/core/test_pipeline_tools.py \
  tests/gateway/test_sessions_api.py -q
```

Expected: PASS
