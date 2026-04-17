# M-Stream Closeout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish regression cleanup and implement Phase 3 self-repair plus Phase 4 bounded self-restart for Hypo-Agent.

**Architecture:** Start with full regression to establish a clean baseline, then add self-repair and self-restart as incremental behavior around the existing pipeline/slash-command/event system. Prefer minimal changes in existing extension points: pipeline prompt assembly, builtin slash commands, startup lifespan, and builtin tool registration.

**Tech Stack:** Python, FastAPI, pytest, WebSocket event stream, TypeScript/Vitest for web tests.

---

### Task 1: Baseline regression
- Run backend tests, inspect failures, fix and rerun.
- Run frontend tests, inspect failures, fix and rerun.
- Record each material fix in `/tmp/m_stream_regression_fixes.md`.

### Task 2: Hygiene and consistency checks
- Scan exception handling, unused imports, TODO/FIXME/HACK, config parity, and coverage for newly added modules.
- Validate enabled skills instantiate, models checker runs, and frontend/backend event names align.

### Task 3: Phase 3 self-repair
- Write failing tests for `/repair` variants.
- Add system-prompt self-repair guidance where persona/system prompt is assembled.
- Implement diagnostic slash command flow with graceful fallback when coder tool is unavailable.
- Rerun targeted then full tests.

### Task 4: Phase 4 self-restart
- Write failing tests for cooldown, lock creation, emitter notifications, and `/restart` prompt behavior.
- Implement bounded restart helper and builtin restart tool.
- Wire startup post-restart health handling and deployment config.
- Rerun targeted then full tests.

### Task 5: Final verification
- Run full pytest, web tests, and `ruff` if configured.
- Summarize changed files, fixes, remaining blockers, and test counts in final report.
