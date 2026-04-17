# Notion Heartbeat Today-Match Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify Notion heartbeat todo matching so heartbeat reads tasks due today or spanning today by default, always shows `父任务 / 子任务`, and keeps the matching mode configurable.

**Architecture:** Centralize todo normalization, parent-title hydration, and today-match helpers in `src/hypo_agent/skills/notion_skill.py`. Reuse those helpers from both `src/hypo_agent/skills/notion_heartbeat.py` and `src/hypo_agent/skills/heartbeat_snapshot_skill.py`, with `tasks.yaml` driving the default heartbeat match mode.

**Tech Stack:** Python, Pydantic, pytest, FastAPI app wiring

---

### Task 1: Lock Behavior with Tests

**Files:**
- Modify: `tests/skills/test_notion_skill.py`
- Modify: `tests/skills/test_heartbeat_snapshot_skill.py`
- Modify: `tests/core/test_config_loader.py`

**Step 1: Write the failing tests**

- Add a `NotionSkill` test proving date-range rows normalize to `date_start/date_end/is_date_span/display_title`.
- Add a `NotionSkill` test proving heartbeat source returns a spanning task and formats it as `父任务 / 子任务`.
- Add a `HeartbeatSnapshotSkill` test proving “今日相关” includes a spanning task while daily recurring tasks remain excluded from `high_priority_due_soon`.
- Add a config loader test proving `heartbeat.notion_today_match_mode` accepts `cover_today`.

**Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/skills/test_notion_skill.py tests/skills/test_heartbeat_snapshot_skill.py tests/core/test_config_loader.py -q
```

Expected: FAIL because the new normalized fields, config field, and “今日相关” behavior do not exist yet.

### Task 2: Implement Unified Notion Todo Semantics

**Files:**
- Modify: `src/hypo_agent/skills/notion_skill.py`
- Modify: `src/hypo_agent/skills/notion_heartbeat.py`

**Step 1: Write the minimal implementation**

- Add `today_match_mode` state with default `cover_today`.
- Expose helpers for configuring and reading the mode.
- Extend normalized todo items with `date_start`, `date_end`, `is_date_span`, and `display_title`.
- Add a helper that decides whether an item is “today-related”.
- Change the heartbeat source to query raw rows without the Notion-side day filter, normalize locally, and render `display_title`.

**Step 2: Run targeted tests**

Run:
```bash
uv run pytest tests/skills/test_notion_skill.py -q
```

Expected: PASS

### Task 3: Rewire Heartbeat Snapshot and App Config

**Files:**
- Modify: `src/hypo_agent/skills/heartbeat_snapshot_skill.py`
- Modify: `src/hypo_agent/models.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `config/tasks.yaml`

**Step 1: Write the minimal implementation**

- Add `heartbeat.notion_today_match_mode` to the task config model with default `cover_today`.
- Push the runtime config into `NotionSkill` during app startup.
- Update `HeartbeatSnapshotSkill` to rely on unified normalized todo data and today-match semantics.
- Rename human summary wording from `今日到期未完成` to `今日相关未完成`.

**Step 2: Run targeted tests**

Run:
```bash
uv run pytest tests/skills/test_heartbeat_snapshot_skill.py tests/core/test_config_loader.py -q
```

Expected: PASS

### Task 4: Full Verification

**Files:**
- Modify: none

**Step 1: Run focused verification**

Run:
```bash
uv run pytest tests/skills/test_notion_skill.py tests/skills/test_heartbeat_snapshot_skill.py tests/core/test_config_loader.py tests/gateway/test_app_scheduler_lifecycle.py -q
```

Expected: PASS

**Step 2: Optional smoke in test mode if needed**

Run:
```bash
bash test_run.sh
```

Expected: app boots in test mode on `8766` without touching production adapters.
