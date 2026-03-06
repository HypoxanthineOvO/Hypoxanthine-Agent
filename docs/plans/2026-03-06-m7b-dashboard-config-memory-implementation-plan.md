# M7b Dashboard + Config Editor + Memory Editor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 M7b 端到端交付：修复 Session 切换后工具中间消息丢失，并新增 Dashboard、Config Editor、Memory Editor 的后端 API 与前端页面能力。

**Architecture:** 先修复数据面（`tool_invocations` 持久化与会话历史重建），再扩展 Gateway API（Dashboard/Config/Memory），最后接入 Web 多页面 UI。所有新增接口统一 `require_api_token`，前端请求统一带 `?token=`，并通过 `AppDeps.reload_config()` 形成可热重载配置闭环。

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, Pydantic v2, structlog, pytest, Vue 3 + Vite + TypeScript, Naive UI, ECharts (`vue-echarts` + `echarts`), Monaco (`@monaco-editor/loader`), vitest.

---

## Skills and Constraints

- Execution skills to use: `@test-driven-development` `@verification-before-completion` `@systematic-debugging`
- Security constraint: all **new** REST endpoints must call `require_api_token(request)`.
- Frontend constraint: all fetch/REST calls must include `?token=...`.
- Responsive constraint: keep existing 3-breakpoint behavior (`>=1024`, `768-1023`, `<768`).
- Doc commit rule: docs changes for this milestone must be a separate commit with message `M7b[doc]: <说明>`.
- Git commit rule: 不使用 conventional commits；开发阶段可用临时 working commit，最终必须整理为 3 个提交：
  1) `M7b: Dashboard + Config Editor + Memory Editor`
  2) `M7b[fix]: <修复描述>`（打 tag `v0.7.1`）
  3) `M7b[doc]: <文档描述>`

---

## Phase Overview

1. Phase A: Tool invocation persistence hardening + chat history reconstruction
2. Phase B: Backend API foundation (Dashboard + Config + Memory)
3. Phase C: App wiring and config hot reload
4. Phase D: Frontend multi-view shell + three feature pages
5. Phase E: End-to-end tests and docs handoff

---

### Task 1: Upgrade `tool_invocations` schema to M7b contract

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Test: `tests/memory/test_structured_store.py`

**Step 1: Write failing tests for new table columns and insert/read behavior**
Run: `pytest tests/memory/test_structured_store.py::test_structured_store_tool_invocations_schema_matches_m7b tests/memory/test_structured_store.py::test_structured_store_record_tool_invocation_with_compressed_meta -q`
Expected: FAIL (new tests fail on missing columns/fields)

**Step 2: Run the tests to verify failure reason is schema mismatch**
Run: `pytest tests/memory/test_structured_store.py::test_structured_store_tool_invocations_schema_matches_m7b -q`
Expected: FAIL with missing `skill_name` / `params_json` / `result_summary` / `compressed_meta_json`

**Step 3: Implement schema + migration-safe alter logic**
- In `init()`, ensure `tool_invocations` has columns:
  - `id`, `session_id`, `tool_name`, `skill_name`, `params_json`, `status`, `result_summary`, `duration_ms`, `error_info`, `compressed_meta_json`, `created_at`
- Add migration path for existing DB:
  - `PRAGMA table_info(tool_invocations)` then `ALTER TABLE ... ADD COLUMN ...` for missing nullable columns
  - preserve existing data and indices
- Update write/read method signatures to use new names (`params_json`, `result_summary`, `compressed_meta_json`)

**Step 4: Run tests and verify pass**
Run: `pytest tests/memory/test_structured_store.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/memory/structured_store.py tests/memory/test_structured_store.py
git commit -m "M7b working: tool_invocations schema migration"
```

---

### Task 2: Persist complete invocation records in `SkillManager.invoke()`

**Files:**
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/models.py`
- Test: `tests/skills/test_skill_manager.py`

**Step 1: Write failing tests for `skill_name`, normalized status, and JSON payload fields**
Run: `pytest tests/skills/test_skill_manager.py::test_skill_manager_records_invocation_with_skill_name_and_params_json tests/skills/test_skill_manager.py::test_skill_manager_records_timeout_and_blocked_status -q`
Expected: FAIL

**Step 2: Confirm failures are due to incomplete persisted payload**
Run: `pytest tests/skills/test_skill_manager.py::test_skill_manager_records_invocation_with_skill_name_and_params_json -q`
Expected: FAIL with assertion mismatch on stored keys/value names

**Step 3: Implement minimal invoke-path persistence updates**
- Ensure all branches (`success/error/timeout/blocked`) call store write with:
  - `skill_name`
  - `params_json`
  - `result_summary` (truncate 500 chars)
  - `status` normalized to `success/error/timeout/blocked`
- Return inserted `invocation_id` via `SkillOutput.metadata["invocation_id"]` for downstream update hooks

**Step 4: Run tests and verify pass**
Run: `pytest tests/skills/test_skill_manager.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/skill_manager.py src/hypo_agent/models.py tests/skills/test_skill_manager.py
git commit -m "M7b working: persist invocation payload in skill manager"
```

---

### Task 3: Attach compression metadata back to invocation row

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/core/test_pipeline_tools.py`
- Test: `tests/memory/test_structured_store.py`

**Step 1: Write failing tests for `compressed_meta_json` update after compressor runs**
Run: `pytest tests/core/test_pipeline_tools.py::test_pipeline_updates_invocation_compressed_meta tests/memory/test_structured_store.py::test_structured_store_updates_invocation_compressed_meta -q`
Expected: FAIL

**Step 2: Verify failure traces point to missing update method and pipeline hook**
Run: `pytest tests/core/test_pipeline_tools.py::test_pipeline_updates_invocation_compressed_meta -q`
Expected: FAIL with method-not-found or no DB update

**Step 3: Implement update hook**
- Add store method `update_tool_invocation_compressed_meta(invocation_id, compressed_meta_json)`
- In pipeline:
  - read `invocation_id` from `output.metadata`
  - after `compress_if_needed` and `compressed_meta` exists, update row
  - keep WS payload unchanged (`compressed_meta` still emitted in `tool_call_result`)

**Step 4: Run tests and verify pass**
Run: `pytest tests/core/test_pipeline_tools.py tests/memory/test_structured_store.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/memory/structured_store.py src/hypo_agent/core/pipeline.py tests/core/test_pipeline_tools.py tests/memory/test_structured_store.py
git commit -m "M7b working: store compressed meta on tool invocations"
```

---

### Task 4: Add authenticated session invocation history API

**Files:**
- Modify: `src/hypo_agent/gateway/sessions_api.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/gateway/test_sessions_api.py`

**Step 1: Write failing API tests for `/api/sessions/{session_id}/tool-invocations`**
Run: `pytest tests/gateway/test_sessions_api.py::test_get_session_tool_invocations_requires_token tests/gateway/test_sessions_api.py::test_get_session_tool_invocations_returns_rows -q`
Expected: FAIL

**Step 2: Run failure test to confirm endpoint missing**
Run: `pytest tests/gateway/test_sessions_api.py::test_get_session_tool_invocations_returns_rows -q`
Expected: FAIL with 404 or route missing

**Step 3: Implement new route**
- `GET /api/sessions/{session_id}/tool-invocations`
- call `require_api_token(request)`
- read from `structured_store.list_tool_invocations(session_id=session_id)`
- return JSON list with M7b fields

**Step 4: Run tests and verify pass**
Run: `pytest tests/gateway/test_sessions_api.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/sessions_api.py src/hypo_agent/gateway/app.py tests/gateway/test_sessions_api.py
git commit -m "M7b working: add session tool invocations api"
```

---

### Task 5: Rebuild Chat history by merging message + invocation timelines

**Files:**
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/types/message.ts`
- Test: `web/src/views/__tests__/ChatView.spec.ts`

**Step 1: Write failing frontend tests for merged history and tokenized fetch**
Run: `cd web && npm run test -- --run src/views/__tests__/ChatView.spec.ts`
Expected: FAIL (no merged history, no token query on REST calls)

**Step 2: Verify failure points**
Run: `cd web && npm run test -- --run src/views/__tests__/ChatView.spec.ts -t "loads merged message and tool invocation history"`
Expected: FAIL with missing tool events in rendered timeline

**Step 3: Implement merge loader**
- `loadSessionMessages()` fetch both:
  - `/api/sessions/{id}/messages?token=...`
  - `/api/sessions/{id}/tool-invocations?token=...`
- reconstruct 2 synthetic events per invocation:
  - `tool_call_start` (params from `params_json`)
  - `tool_call_result` (status/result/error/compressed_meta)
- merge with original messages by timestamp (`created_at` vs `timestamp`), stable-order start before result

**Step 4: Run tests and verify pass**
Run: `cd web && npm run test -- --run src/views/__tests__/ChatView.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/views/ChatView.vue web/src/types/message.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "M7b working: merge tool history into chat session restore"
```

---

### Task 6: Add Dashboard backend API set

**Files:**
- Create: `src/hypo_agent/gateway/dashboard_api.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/gateway/test_dashboard_api.py`

**Step 1: Write failing tests for all dashboard endpoints**
Run: `pytest tests/gateway/test_dashboard_api.py -q`
Expected: FAIL (module/route missing)

**Step 2: Validate failing state for auth + payload contracts**
Run: `pytest tests/gateway/test_dashboard_api.py::test_dashboard_status_requires_token tests/gateway/test_dashboard_api.py::test_dashboard_token_stats_shape -q`
Expected: FAIL

**Step 3: Implement endpoints**
- `GET /api/dashboard/status`
  - uptime (from app start)
  - session_count
  - kill_switch
  - bwrap_available
- `GET /api/dashboard/token-stats?days=7`
  - per day + per model aggregation from `token_usage`
- `GET /api/dashboard/latency-stats?days=7`
  - daily p50/p95/p99 from `token_usage.latency_ms` (fallback to `tool_invocations.duration_ms` if missing)
- `GET /api/dashboard/recent-tasks?limit=20`
  - latest tool invocations
- `GET /api/dashboard/skills`
  - `skill_manager.list_skills()` + breaker status
- all routes call `require_api_token(request)`

**Step 4: Run tests and verify pass**
Run: `pytest tests/gateway/test_dashboard_api.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/dashboard_api.py src/hypo_agent/memory/structured_store.py src/hypo_agent/gateway/app.py tests/gateway/test_dashboard_api.py
git commit -m "M7b working: dashboard backend apis"
```

---

### Task 7: Add Config API with YAML validation and hot reload

**Files:**
- Create: `src/hypo_agent/gateway/config_api.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/core/config_loader.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/security/circuit_breaker.py`
- Modify: `src/hypo_agent/security/permission_manager.py`
- Modify: `src/hypo_agent/models.py`
- Test: `tests/gateway/test_config_api.py`
- Test: `tests/gateway/test_app_deps_permissions.py`

**Step 1: Write failing tests for file list/read/update and reload side effects**
Run: `pytest tests/gateway/test_config_api.py -q`
Expected: FAIL

**Step 2: Confirm failure on missing routes and reload method**
Run: `pytest tests/gateway/test_config_api.py::test_config_put_validates_yaml_before_write -q`
Expected: FAIL

**Step 3: Implement Config API + reload mechanism**
- routes:
  - `GET /api/config/files`
  - `GET /api/config/{filename}`
  - `PUT /api/config/{filename}`
- editable whitelist:
  - `models.yaml`, `skills.yaml`, `security.yaml`, `persona.yaml`, `tasks.yaml`
- validation:
  - `models.yaml` -> `ModelConfig`
  - `security.yaml` -> `GatewaySettings`/`SecurityConfig` split validation
  - `persona.yaml` -> `PersonaConfig`
  - `skills.yaml` + `tasks.yaml` -> dedicated Pydantic schema in `models.py`
- add `AppDeps.reload_config()`:
  - reload model routing config
  - reload enabled skills and tool registrations
  - reload whitelist rules and circuit breaker config
  - keep app state references in sync (`app.state.*`)

**Step 4: Run tests and verify pass**
Run: `pytest tests/gateway/test_config_api.py tests/gateway/test_app_deps_permissions.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/config_api.py src/hypo_agent/gateway/app.py src/hypo_agent/core/config_loader.py src/hypo_agent/core/skill_manager.py src/hypo_agent/security/circuit_breaker.py src/hypo_agent/security/permission_manager.py src/hypo_agent/models.py tests/gateway/test_config_api.py tests/gateway/test_app_deps_permissions.py
git commit -m "M7b working: config api with validation and hot reload"
```

---

### Task 8: Add Memory API (L2 tables, L3 files, session export)

**Files:**
- Create: `src/hypo_agent/gateway/memory_api.py`
- Modify: `src/hypo_agent/gateway/sessions_api.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/memory/session.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Test: `tests/gateway/test_memory_api.py`
- Test: `tests/gateway/test_sessions_api.py`

**Step 1: Write failing tests for memory endpoints and export**
Run: `pytest tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py -q`
Expected: FAIL

**Step 2: Verify missing route and writable restriction failures**
Run: `pytest tests/gateway/test_memory_api.py::test_memory_tables_put_denies_non_writable_table -q`
Expected: FAIL

**Step 3: Implement APIs**
- `GET /api/memory/tables`
- `GET /api/memory/tables/{name}?page=1&size=50`
- `PUT /api/memory/tables/{name}/{id}` (only `preferences` writable in backend dict)
- `GET /api/memory/files`
- `GET /api/memory/files/{path}`
- `PUT /api/memory/files/{path}`
- `GET /api/sessions/{id}/export?format=json|markdown`
- optional support endpoint for UI delete:
  - `DELETE /api/sessions/{id}` (clear jsonl + store metadata when present)
- all new routes use `require_api_token`
- file APIs strictly confine to `memory/knowledge` with traversal protection

**Step 4: Run tests and verify pass**
Run: `pytest tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/memory_api.py src/hypo_agent/gateway/sessions_api.py src/hypo_agent/gateway/app.py src/hypo_agent/memory/session.py src/hypo_agent/memory/structured_store.py tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py
git commit -m "M7b working: memory apis for l1 l2 l3 and export"
```

---

### Task 9: Wire new routers and app runtime state

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/gateway/test_main.py`
- Test: `tests/gateway/test_sessions_api_cors.py`

**Step 1: Write failing tests for router registration and app state wiring**
Run: `pytest tests/gateway/test_main.py tests/gateway/test_sessions_api_cors.py -q`
Expected: FAIL (new routers not included yet)

**Step 2: Confirm failure details**
Run: `pytest tests/gateway/test_main.py::test_create_app_registers_m7b_routers -q`
Expected: FAIL

**Step 3: Implement app wiring**
- include `dashboard_api_router`, `config_api_router`, `memory_api_router`
- set app startup timestamp for uptime
- ensure `app.state.deps.reload_config` callable paths can be invoked by config API

**Step 4: Run tests and verify pass**
Run: `pytest tests/gateway/test_main.py tests/gateway/test_sessions_api_cors.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/app.py tests/gateway/test_main.py tests/gateway/test_sessions_api_cors.py
git commit -m "M7b working: wire routers and runtime app state"
```

---

### Task 10: Enable multi-view shell navigation in frontend

**Files:**
- Modify: `web/src/App.vue`
- Modify: `web/src/components/layout/SideNav.vue`
- Create: `web/src/views/DashboardView.vue`
- Create: `web/src/views/ConfigView.vue`
- Create: `web/src/views/MemoryView.vue`

**Step 1: Write failing UI tests for nav switch and active view rendering**
Run: `cd web && npm run test -- --run src/views/__tests__/ChatView.spec.ts`
Expected: FAIL after adding new assertions for shell switching

**Step 2: Verify failure is from disabled nav and single-view rendering**
Run: `cd web && npm run test -- --run -t "switches between chat dashboard config memory views"`
Expected: FAIL

**Step 3: Implement shell switch logic**
- remove `disabled: true` on Dashboard/Config/Memory nav items
- `App.vue` stores `activeView` and renders corresponding page
- keep existing sidebar collapse behavior for 3 breakpoints

**Step 4: Run tests and verify pass**
Run: `cd web && npm run test -- --run`
Expected: PASS for touched tests

**Step 5: Commit**
```bash
git add web/src/App.vue web/src/components/layout/SideNav.vue web/src/views/DashboardView.vue web/src/views/ConfigView.vue web/src/views/MemoryView.vue
git commit -m "M7b working: enable multi-view shell navigation"
```

---

### Task 11: Build DashboardView with charts and polling

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/src/views/DashboardView.vue`
- Create: `web/src/views/__tests__/DashboardView.spec.ts`

**Step 1: Write failing tests for API calls, rendering, and polling refresh**
Run: `cd web && npm run test -- --run src/views/__tests__/DashboardView.spec.ts`
Expected: FAIL (view/dependencies missing)

**Step 2: Install chart deps and verify test environment compiles**
Run: `cd web && npm i vue-echarts echarts`
Expected: install success, tests still FAIL before implementation

**Step 3: Implement Dashboard UI**
- status cards (`uptime`, `session_count`, `kill_switch`, `bwrap_available`)
- token chart by model/day
- latency chart for p50/p95/p99
- skills list with `🟢/🔴/⏳`
- recent task table
- polling every 5s with teardown on unmount
- all URLs append `?token=...`

**Step 4: Run tests and verify pass**
Run: `cd web && npm run test -- --run src/views/__tests__/DashboardView.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/package.json web/package-lock.json web/src/views/DashboardView.vue web/src/views/__tests__/DashboardView.spec.ts
git commit -m "M7b working: dashboard view with charts and polling"
```

---

### Task 12: Build ConfigView with Form/YAML dual mode and Monaco

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/src/components/editor/MonacoEditor.vue`
- Create: `web/src/views/ConfigView.vue`
- Create: `web/src/views/__tests__/ConfigView.spec.ts`

**Step 1: Write failing tests for file switch, yaml edit, form edit, and save**
Run: `cd web && npm run test -- --run src/views/__tests__/ConfigView.spec.ts`
Expected: FAIL

**Step 2: Install editor and yaml deps**
Run: `cd web && npm i @monaco-editor/loader yaml`
Expected: install success, tests still FAIL before implementation

**Step 3: Implement ConfigView**
- load list from `/api/config/files?token=...`
- YAML tab with Monaco loader
- Form tab:
  - `models.yaml`: model table + task routing KV
  - `skills.yaml`: switches
  - `security.yaml`: breaker numeric fields + whitelist table
  - `persona.yaml` / `tasks.yaml`: show fallback hint
- save with `PUT /api/config/{filename}?token=...`
- success/error feedback with Naive UI message/notification

**Step 4: Run tests and verify pass**
Run: `cd web && npm run test -- --run src/views/__tests__/ConfigView.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/package.json web/package-lock.json web/src/components/editor/MonacoEditor.vue web/src/views/ConfigView.vue web/src/views/__tests__/ConfigView.spec.ts
git commit -m "M7b working: config editor monaco and form mode"
```

---

### Task 13: Build MemoryView (L1/L2/L3 editor)

**Files:**
- Create: `web/src/views/MemoryView.vue`
- Create: `web/src/views/__tests__/MemoryView.spec.ts`
- Modify: `web/src/views/ChatView.vue`

**Step 1: Write failing tests for L1/L2/L3 tabs and key actions**
Run: `cd web && npm run test -- --run src/views/__tests__/MemoryView.spec.ts`
Expected: FAIL

**Step 2: Confirm missing rendering and actions**
Run: `cd web && npm run test -- --run -t "renders l1 l2 l3 memory sections"`
Expected: FAIL

**Step 3: Implement MemoryView**
- L1:
  - session list
  - history preview
  - delete
  - export json/markdown
- L2:
  - table list with row_count/writable
  - paged row browser
  - edit only writable table (`preferences`)
- L3:
  - file list + search filter
  - Monaco editor
  - save back to API
- all URLs append `?token=...`

**Step 4: Run tests and verify pass**
Run: `cd web && npm run test -- --run src/views/__tests__/MemoryView.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/views/MemoryView.vue web/src/views/__tests__/MemoryView.spec.ts web/src/views/ChatView.vue
git commit -m "M7b working: memory editor for l1 l2 l3"
```

---

### Task 14: Add backend integration coverage for M7b APIs

**Files:**
- Create: `tests/gateway/test_dashboard_api.py`
- Create: `tests/gateway/test_config_api.py`
- Create: `tests/gateway/test_memory_api.py`
- Modify: `tests/gateway/test_sessions_api.py`

**Step 1: Ensure all new tests fail before final implementation check**
Run: `pytest tests/gateway/test_dashboard_api.py tests/gateway/test_config_api.py tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py -q`
Expected: FAIL at start of this task

**Step 2: Implement remaining missing assertions and fixtures**
- token auth checks for all new endpoints
- parameter validation (`days`, `limit`, `page`, `size`)
- config validation error payload assertions
- memory writable restrictions and path traversal defense assertions

**Step 3: Run targeted suite**
Run: `pytest tests/gateway/test_dashboard_api.py tests/gateway/test_config_api.py tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py -q`
Expected: PASS

**Step 4: Run full backend suite**
Run: `pytest`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/gateway/test_dashboard_api.py tests/gateway/test_config_api.py tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py
git commit -m "M7b working: backend coverage for dashboard config memory apis"
```

---

### Task 15: Add frontend integration coverage and final verification

**Files:**
- Modify: `web/src/views/__tests__/ChatView.spec.ts`
- Create: `web/src/views/__tests__/DashboardView.spec.ts`
- Create: `web/src/views/__tests__/ConfigView.spec.ts`
- Create: `web/src/views/__tests__/MemoryView.spec.ts`

**Step 1: Ensure new tests exist for token query and major user flows**
Run: `cd web && npm run test -- --run src/views/__tests__/ChatView.spec.ts src/views/__tests__/DashboardView.spec.ts src/views/__tests__/ConfigView.spec.ts src/views/__tests__/MemoryView.spec.ts`
Expected: PASS after implementation

**Step 2: Add any missing edge-case assertions**
- session switch keeps tool history
- dashboard polling cleanup on unmount
- config save error rendering
- memory write path denial message

**Step 3: Run full frontend suite**
Run: `cd web && npm run test -- --run`
Expected: PASS

**Step 4: Run build verification**
Run: `cd web && npm run build`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/views/__tests__/ChatView.spec.ts web/src/views/__tests__/DashboardView.spec.ts web/src/views/__tests__/ConfigView.spec.ts web/src/views/__tests__/MemoryView.spec.ts
git commit -m "M7b working: frontend coverage for dashboard config memory views"
```

---

### Task 16: Update docs and milestone handoff

**Files:**
- Modify: `docs/architecture.md`
- Create/Modify: `docs/runbooks/*` (only if new operator procedures are required)
- Create: `docs/plans/2026-03-06-m7b-dashboard-config-memory-implementation-plan.md`

**Step 1: Write doc updates describing M7b APIs and UI surfaces**
- add section under architecture for:
  - tool invocation history persistence + replay
  - dashboard/config/memory endpoints
  - config hot reload behavior

**Step 2: Validate doc accuracy against code**
Run: `rg -n "dashboard|tool-invocations|reload_config|memory/tables|config/files" src/hypo_agent web/src docs/architecture.md`
Expected: strings exist and match implemented names

**Step 3: Final verification command set**
Run: `pytest && (cd web && npm run test -- --run) && (cd web && npm run build)`
Expected: PASS

**Step 4: Create doc-only commit (required by AGENTS.md)**
```bash
git add docs/architecture.md docs/plans/2026-03-06-m7b-dashboard-config-memory-implementation-plan.md
git commit -m "M7b[doc]: document dashboard config memory implementation and API contracts"
```

**Step 5: Squash working commits into final 3-commit history**
- 在合并前整理历史，仅保留以下 3 个提交（按顺序）：
  1. `M7b: Dashboard + Config Editor + Memory Editor`
  2. `M7b[fix]: <修复描述>`
  3. `M7b[doc]: <文档描述>`
- 在第 2 个提交上打 tag：`v0.7.1`
- 确保不出现 `feat(...)` / `fix(...)` / `test(...)` 等 conventional commit 形式。

**Step 6: Prepare PR summary**
- include:
  - API matrix
  - DB migration notes
  - UI screenshots/GIFs
  - test evidence

---

## Definition of Done (M7b)

- Session switch restores both text messages and tool invocation messages.
- `tool_invocations` schema matches M7b and stores compressed meta.
- Dashboard APIs + page complete with 5s polling.
- Config APIs + page complete with YAML validation and hot reload.
- Memory APIs + page complete for L1/L2/L3 browse/edit/export flows.
- All new endpoints authenticated via `require_api_token`.
- Frontend REST requests consistently append `?token=...`.
- `pytest`, frontend vitest, and frontend build all pass.
- Architecture docs updated and doc commit follows `M7b[doc]: ...`.
