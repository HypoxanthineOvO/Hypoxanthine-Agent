# Hybrid Skill Knowledge-Layer Migration Phase 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the knowledge layer of the first batch of B-class Hybrid Skills into `SKILL.md`, keep their Python backends as execution adapters, and split `info_reach` guidance into query vs subscription responsibilities without regressing existing behavior.

**Architecture:** Keep `SkillManager` as the execution substrate for Hybrid Skills and use `SkillCatalog` only for declarative knowledge injection. Each migrated Hybrid Skill gets a `skills/hybrid/<name>/SKILL.md` that teaches tool selection, sequencing, parameter filling, and output interpretation, while the Python backend keeps API auth, transport, conversion, heartbeat hooks, and attachment handling. For `info_reach`, keep all tools registered but move the “what to use when” policy into `SKILL.md`, making the boundary with `info-portal` explicit.

**Tech Stack:** Python 3.12, FastAPI, asyncio, httpx, aiosqlite, structlog, YAML frontmatter, pytest

---

## Context and Preconditions

- Primary references read before this plan:
  - `AGENTS.md`
  - `docs/audit_report_full.md` as the local mirror for the requested audit basis
  - `skills/SPEC.md`
  - `src/hypo_agent/core/skill_catalog.py`
  - `src/hypo_agent/core/pipeline.py`
  - `src/hypo_agent/core/exec_profiles.py`
  - `src/hypo_agent/skills/agent_search_skill.py`
  - `src/hypo_agent/skills/info_portal_skill.py`
  - `src/hypo_agent/skills/notion_skill.py`
  - `src/hypo_agent/skills/coder_skill.py`
  - `src/hypo_agent/skills/probe_skill.py`
  - `src/hypo_agent/skills/info_reach_skill.py`
- Important current-state findings:
  - `info_skill.py` no longer exists; the active “info” backend is `src/hypo_agent/skills/info_portal_skill.py`.
  - `NotionSkill` does contain a heartbeat event source registration path through `heartbeat_service.register_event_source("notion_todo", ...)`.
  - `ProbeSkill` still wraps an in-process `ProbeServer` object and assembles `Attachment` payloads itself, so Phase 2 should treat it as “usage-contract migration only”, not backend extraction.
  - `SkillCatalog` and Pipeline candidate prompt injection are already live from Phase 1.
  - The worktree is dirty; do not revert unrelated changes or assume clean commit boundaries.

## Execution Constraints

- Do not implement until manual confirmation.
- Keep Python execution behavior intact; move usage guidance out of tool descriptions, but do not remove API, conversion, attachment, heartbeat, or webhook logic.
- Validate each Part immediately after implementation instead of deferring all tests to the end.
- If a requirement from the audit does not match the current code, record `[未发现，跳过]` or `[需讨论]` with rationale rather than blocking.
- Because the repo is already dirty, commit steps may need to be skipped or reported as blocked if safe isolation is not possible.

## Deliverable Breakdown

1. Part D: `agent_search` hybrid knowledge-layer migration
2. Part E: `info-portal` hybrid knowledge-layer migration
3. Part F: `notion` hybrid migration + heartbeat todo source audit
4. Part G: `coder` hybrid migration
5. Part H: `probe` usage-contract migration
6. Part I: `info_reach` split-guidance migration
7. Part J: index/config updates + final regression

---

### Task D: Migrate `agent_search` to Hybrid `SKILL.md`

**Files:**
- Create: `skills/hybrid/agent-search/SKILL.md`
- Modify: `src/hypo_agent/skills/agent_search_skill.py`
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing agent search test file(s) discovered in repo

**Step 1: Write failing SkillCatalog assertions**

Add tests that verify:
- `SkillCatalog` scans `skills/hybrid/agent-search/SKILL.md`
- the manifest name is `agent-search`
- triggers like `搜索` or `search` match it

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k agent_search
```

Expected:
- FAIL because the new `SKILL.md` does not exist yet

**Step 2: Create `skills/hybrid/agent-search/SKILL.md`**

Frontmatter must include:
- `name: "agent-search"`
- `allowed-tools: "web_search web_read"`
- `metadata.hypo.backend: "agent_search"`
- low-risk trigger set for web lookup / verification

Body must cover:
- when to use `web_search` vs `web_read`
- “search first, then read” strategy
- `query` and `max_results` guidance
- result interpretation patterns
- at least three scenario examples

**Step 3: Trim Python tool descriptions**

In `src/hypo_agent/skills/agent_search_skill.py`:
- keep the Tavily API wiring exactly as-is
- reduce `web_search` and `web_read` descriptions to short functional statements
- remove any explanatory copy that now belongs in `SKILL.md`

**Step 4: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k agent_search
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add skills/hybrid/agent-search/SKILL.md skills/index.md src/hypo_agent/skills/agent_search_skill.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): D - agent_search hybrid migration"
```

If commit isolation is unsafe because of unrelated dirty changes, record that in the completion report instead of forcing a commit.

---

### Task E: Migrate `info-portal` to Hybrid `SKILL.md`

**Files:**
- Create: `skills/hybrid/info-portal/SKILL.md`
- Modify: `src/hypo_agent/skills/info_portal_skill.py`
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing info portal / info registration tests

**Step 1: Add failing catalog tests**

Add assertions that:
- `info-portal` is scanned
- news / benchmark / trend keywords match it

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k info_portal
```

Expected:
- FAIL

**Step 2: Create `skills/hybrid/info-portal/SKILL.md`**

Frontmatter must declare:
- `allowed-tools: "info_today info_search info_benchmark info_sections"`
- backend `info`

Body must cover:
- exact role of each of the four tools
- suggested calling order (`info_sections` first when the user’s section is unclear)
- how to present returned summaries to the user

**Step 3: Trim backend descriptions**

In `src/hypo_agent/skills/info_portal_skill.py`:
- keep client calls, formatting, and validation unchanged
- simplify tool descriptions to concise capability descriptions
- remove tutorial-style copy now covered by `SKILL.md`

**Step 4: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k "info_portal or info_skill_registration"
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add skills/hybrid/info-portal/SKILL.md skills/index.md src/hypo_agent/skills/info_portal_skill.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): E - info hybrid migration"
```

---

### Task F: Migrate `notion` and audit heartbeat todo source

**Files:**
- Create: `skills/hybrid/notion/SKILL.md`
- Create (optional): `skills/hybrid/notion/references/property-types.md`
- Modify: `src/hypo_agent/skills/notion_skill.py`
- Possibly modify: `src/hypo_agent/core/heartbeat.py` or another heartbeat integration file if actual extraction is required
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing Notion skill tests

**Step 1: Add failing catalog tests**

Add tests that:
- `notion` manifest scans correctly
- `notion` keywords match candidates
- references lazy-load when present

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k notion
```

Expected:
- FAIL

**Step 2: Create `skills/hybrid/notion/SKILL.md` and references**

Body must include:
- tool selection workflow
- requirement to call `notion_get_schema` before database writes/filters
- property type mapping guidance
- several example workflows

If the property mapping table is long, move it into `skills/hybrid/notion/references/property-types.md` and mention it from the body.

**Step 3: Audit the heartbeat todo source**

Inspect `src/hypo_agent/skills/notion_skill.py` and adjacent heartbeat wiring:
- confirm how `notion_todo` is registered
- decide whether the hook should be extracted or left in place for this phase

Decision rule:
- if extraction is small and localized, move registration or callback wiring into heartbeat-side composition code
- otherwise record `[未发现需要拆分的可安全边界，保留现状]` in the final report and keep backend behavior unchanged

**Step 4: Trim backend descriptions only**

In `src/hypo_agent/skills/notion_skill.py`:
- preserve Notion API calls, page/property conversion, JSON parsing, and error normalization
- reduce long tool guidance text to short capability descriptions
- do not remove schema / property conversion helpers

**Step 5: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k notion
```

Expected:
- PASS

**Step 6: Commit**

```bash
git add skills/hybrid/notion/SKILL.md skills/hybrid/notion/references/property-types.md skills/index.md src/hypo_agent/skills/notion_skill.py src/hypo_agent/core/heartbeat.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): F - notion hybrid migration"
```

Remove any untouched file from `git add` if the heartbeat hook audit concludes no code change is needed.

---

### Task G: Migrate `coder` to Hybrid `SKILL.md`

**Files:**
- Create: `skills/hybrid/coder/SKILL.md`
- Modify: `src/hypo_agent/skills/coder_skill.py`
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing coder skill / registration tests

**Step 1: Add failing catalog tests**

Add assertions for:
- `coder` manifest scan
- coding delegation keywords matching

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k coder
```

Expected:
- FAIL

**Step 2: Create `skills/hybrid/coder/SKILL.md`**

Body must explain:
- task submission flow
- per-tool purpose
- prompt construction best practices
- polling cadence
- 10-minute reminder threshold

**Step 3: Trim backend descriptions**

In `src/hypo_agent/skills/coder_skill.py`:
- keep HTTP, webhook URL, status formatting, and summary logic
- shorten descriptions to plain functional summaries

**Step 4: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k coder
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add skills/hybrid/coder/SKILL.md skills/index.md src/hypo_agent/skills/coder_skill.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): G - coder hybrid migration"
```

---

### Task H: Audit `probe` and add Hybrid usage contract

**Files:**
- Create: `skills/hybrid/probe/SKILL.md`
- Modify: `src/hypo_agent/skills/probe_skill.py`
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing probe tests

**Step 1: Record the migration depth decision**

Based on current code:
- `ProbeSkill` depends on in-process `ProbeServer`
- screenshot handling returns `Attachment`

Decision for this phase:
- keep backend structure intact
- add `SKILL.md`
- only trim tool descriptions
- mark the backend extraction as `[需讨论，延后]`

**Step 2: Add failing catalog tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k probe
```

Expected:
- FAIL

**Step 3: Create `skills/hybrid/probe/SKILL.md`**

Body must explain:
- `probe_list_devices` first
- when to use screenshot vs process list vs historical screenshots
- that screenshots return attachments
- remote inspection safety boundaries

**Step 4: Trim backend descriptions**

In `src/hypo_agent/skills/probe_skill.py`:
- keep RPC calls and attachment assembly intact
- shorten descriptions to concise tool summaries

**Step 5: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k probe
```

Expected:
- PASS

**Step 6: Commit**

```bash
git add skills/hybrid/probe/SKILL.md skills/index.md src/hypo_agent/skills/probe_skill.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): H - probe hybrid migration"
```

---

### Task I: Split `info_reach` guidance into query vs subscription responsibilities

**Files:**
- Create: `skills/hybrid/info-reach/SKILL.md`
- Modify: `src/hypo_agent/skills/info_reach_skill.py`
- Modify: `skills/index.md`
- Test: `tests/core/test_skill_catalog.py`
- Test: existing info reach tests

**Step 1: Audit overlap with `info-portal`**

Before changing code, write down the boundary:
- `info_today` / `info_search` / `info_benchmark` / `info_sections` are user-facing portal queries
- `info_query` / `info_summary` are proactive-intelligence queries against `/api/agent/*`
- subscriptions remain in `info_reach`

Likely decision:
- keep all five tools registered
- make `SKILL.md` explicitly steer ordinary “news lookup” questions toward `info-portal`
- reserve `info_reach` query tools for TrendRadar / proactive / subscription-related workflows

**Step 2: Add failing catalog tests**

Add assertions for:
- `info-reach` scan and trigger match
- co-existence with `info-portal`

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k info_reach
```

Expected:
- FAIL

**Step 3: Create `skills/hybrid/info-reach/SKILL.md`**

Body must clearly separate:
- active query use cases
- subscription management use cases
- parameter guidance (`topic`, `frequency`, `channel` as applicable to the real API)
- boundary with `info-portal`

**Step 4: Trim backend descriptions**

In `src/hypo_agent/skills/info_reach_skill.py`:
- keep Hypo-Info API access, aiosqlite subscription CRUD, heartbeat push logic, and structured formatting
- reduce tutorial-style tool descriptions to concise operational descriptions

**Step 5: Run focused tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
pytest -q tests -k info_reach
```

Expected:
- PASS

**Step 6: Commit**

```bash
git add skills/hybrid/info-reach/SKILL.md skills/index.md src/hypo_agent/skills/info_reach_skill.py tests/core/test_skill_catalog.py
git commit -m "feat(skills): I - info_reach hybrid migration"
```

---

### Task J: Finish index, config comments, and full regression

**Files:**
- Modify: `skills/index.md`
- Modify: `config/skills.yaml`
- Modify: tests touched by catalog/index/config comment expectations if any

**Step 1: Update index**

Ensure `skills/index.md` lists:
- all Phase 1 pure skills
- all Phase 2 hybrid skills

**Step 2: Add `config/skills.yaml` comments**

For active Python Skills, add concise comments describing:
- whether a corresponding `SKILL.md` now exists
- whether the Python class is still execution-only backend

Keep YAML valid and avoid changing semantic config values unintentionally.

**Step 3: Run final regression**

Run:
```bash
pytest -q
```

Expected:
- PASS

**Step 4: Completion report**

Report:
- per-part status table
- tests run and results
- any `[未发现，跳过]` or `[需讨论]` decisions
- commit blockers if the dirty worktree prevents safe commits

**Step 5: Commit**

```bash
git add skills/index.md config/skills.yaml
git commit -m "feat(skills): J - hybrid skill migration wrap-up"
```

Again, if the dirty worktree makes an isolated commit unsafe, report the blocker instead of forcing it.
