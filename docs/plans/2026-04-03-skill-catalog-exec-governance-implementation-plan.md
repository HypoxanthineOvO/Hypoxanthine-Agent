# Exec Governance + SkillCatalog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build ExecSkill profile-based command governance, unify runtime Skill boundaries, add skill-aware invocation metadata and CircuitBreaker dimensions, introduce `skills/` + `SkillCatalog` infrastructure, and migrate/create the first 6 `SKILL.md` artifacts without breaking existing tool-calling behavior.

**Architecture:** Keep the current Python Skill system (`BaseSkill` / `SkillManager` / ReAct tool schema) as the execution substrate, and add a parallel SkillCatalog layer for declarative `SKILL.md` discovery and prompt injection. ExecSkill remains the concrete subprocess backend, but gains policy enforcement via named exec profiles loaded from config and optionally selected from `SKILL.md` metadata. Pipeline integration must be backward compatible: when SkillCatalog is absent or no candidate skills match, behavior remains identical to current production.

**Tech Stack:** Python 3.12, FastAPI, asyncio, PyYAML, Pydantic/dataclasses, pytest, structlog, SQLite, markdown-based skill manifests

---

## Context and Preconditions

- Primary local references read for this plan:
  - `AGENTS.md`
  - `docs/architecture.md`
  - `docs/audit_report_full.md` (used as local mirror of the requested Codex audit basis)
  - `src/hypo_agent/skills/base.py`
  - `src/hypo_agent/core/skill_manager.py`
  - `src/hypo_agent/core/pipeline.py`
  - `src/hypo_agent/skills/exec_skill.py`
  - `config/skills.yaml`
- Important current-state observations:
  - `ExecSkill` currently has no permission/profile gating and executes arbitrary shell commands.
  - `_register_enabled_skills()` in `src/hypo_agent/gateway/app.py` is the current source of truth for runtime Python skill registration.
  - `config/skills.yaml` still contains `qq.enabled`, which is a channel switch, not a Python Skill.
  - `memory` is always registered in app wiring but has no explicit `skills.yaml` entry.
  - `CircuitBreaker` currently keys failures by `(session_id, tool_name)` only.
  - `SkillManager` invocation persistence currently records `tool_name` but no logical `skill_name`.
  - `LogInspectorSkill` is still a Python skill and is currently enabled by config.

## Execution Constraints

- Do not implement from this plan until manual confirmation.
- Part A must be completed and fully regression-tested before starting Part B.
- Keep backward compatibility whenever SkillCatalog is uninitialized or unused.
- Do not widen filesystem/code execution permissions beyond the explicit profile system.
- Do not remove `TmuxSkill`; keep it optional and disabled by default.
- Use test mode defaults for any smoke/agent validation: `bash test_run.sh` and `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`.

## Deliverable Breakdown

1. Part A: Exec governance, skill boundary cleanup, invocation metadata
2. Part B: `skills/` repository structure, manifest spec, SkillCatalog, Pipeline prompt integration
3. Part C: one migration (`log-inspector`) + five new pure CLI skills
4. Verification: targeted tests per task, then `pytest -q`

---

### Task A1: Add ExecSkill profile-based permission governance

**Files:**
- Create: `config/exec_profiles.yaml`
- Modify: `src/hypo_agent/skills/exec_skill.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/skills/base.py` or adjacent shared model location if a reusable skill metadata hook is needed
- Create/Modify: `tests/skills/test_exec_skill.py`
- Create: `tests/core/test_exec_profiles.py` if profile parsing deserves an isolated test module

**Step 1: Lock down config contract with failing tests**

Add tests for:
- loading named profiles from `config/exec_profiles.yaml`
- allow-prefix pass
- deny-prefix rejection
- default profile fallback for unknown/unprofiled calls
- `exec_script` using the same profile validator path

Run:
```bash
pytest -q tests/skills/test_exec_skill.py tests/core/test_exec_profiles.py
```

Expected:
- FAIL because profile config parsing and validation do not exist yet

**Step 2: Add exec profile config model and loader**

Implement minimal config contract:
- `profiles.<name>.allow_prefixes`
- `profiles.<name>.deny_prefixes`
- support `"*"` in `allow_prefixes` for backward-compatible default profile

Preferred placement:
- either new lightweight config model in `src/hypo_agent/models.py`
- or local dataclass/Pydantic model in a new helper module such as `src/hypo_agent/core/exec_profiles.py`

**Step 3: Add command validation layer to ExecSkill**

Implement before subprocess launch:
- normalize command string
- reject empty/whitespace-only commands as today
- evaluate deny prefixes first
- then evaluate allow prefixes
- if no profile is specified, use `default`
- if `default.allow_prefixes=["*"]`, preserve current broad behavior except explicit denies

Implementation notes:
- keep matching simple and deterministic: normalized string prefix matching is acceptable for V1
- document that this is a shell-safety policy, not a full shell parser
- ensure `exec_script` validates the derived execution command or interpreter profile, not only raw source text

**Step 4: Wire profile selection API without breaking current tool schema**

Backward-compatible path:
- allow `params.get("exec_profile")` internally but do not require it in the tool schema yet
- later SkillCatalog/Pipeline can pass selected profile through execution context
- direct current calls continue to use `default`

**Step 5: Add logging and error shape**

Rejected command should return `SkillOutput(status="error")` with clear reason:
- profile name
- whether it matched deny list or missed allow list
- normalized command snippet

Also log structured fields:
- `tool_name="exec_command"| "exec_script"`
- `exec_profile`
- `command`
- `decision="allowed" | "denied"`
- `deny_reason`

**Step 6: Re-run tests**

Run:
```bash
pytest -q tests/skills/test_exec_skill.py tests/core/test_exec_profiles.py
```

Expected:
- PASS

**Step 7: Commit**

```bash
git add config/exec_profiles.yaml src/hypo_agent/skills/exec_skill.py src/hypo_agent/gateway/app.py tests/skills/test_exec_skill.py tests/core/test_exec_profiles.py
git commit -m "feat(skills): A1 - ExecSkill profile-based permission"
```

---

### Task A2: Unify Skill boundaries and clean `config/skills.yaml`

**Files:**
- Modify: `config/skills.yaml`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `tests/gateway/test_app_deps_permissions.py`
- Modify: `tests/gateway/test_settings.py`
- Create: `tests/gateway/test_skill_loading_smoke.py`

**Step 1: Write failing tests for config/runtime alignment**

Add tests that assert:
- `memory.enabled` exists and is honored
- `tmux.enabled` exists and defaults false
- `info.enabled` exists
- no channel-only key like `qq` is treated as a Python skill
- every enabled Python skill in `config/skills.yaml` is registered by `_register_enabled_skills()`

Run:
```bash
pytest -q tests/gateway/test_app_deps_permissions.py tests/gateway/test_settings.py tests/gateway/test_skill_loading_smoke.py
```

Expected:
- FAIL on config mismatch and missing smoke assertions

**Step 2: Normalize `config/skills.yaml`**

Required changes:
- remove `qq.enabled`
- add `memory.enabled: true`
- keep `tmux.enabled: false`
- ensure `info.enabled: true`
- ensure each Python skill registered by `_register_enabled_skills()` has a config entry

Document any intentionally code-driven registrations separately if unavoidable.

**Step 3: Make registration source explicit**

In `SkillManager` or app wiring, add startup log showing:
- skill name
- origin (`config`, `builtin`, `hardcoded`, `auto`)
- registered tools

Implementation approach:
- easiest is logging from `_register_enabled_skills()` at registration time
- if implemented inside `SkillManager.register()`, add optional `source` argument or companion helper rather than hardcoding unknown origin

**Step 4: Make `memory` config-controlled**

Find current unconditional MemorySkill registration path and gate it through `skills.yaml`.
- keep default enabled in config
- preserve behavior for normal app startup

**Step 5: Re-run tests**

Run:
```bash
pytest -q tests/gateway/test_app_deps_permissions.py tests/gateway/test_settings.py tests/gateway/test_skill_loading_smoke.py
```

Expected:
- PASS

**Step 6: Commit**

```bash
git add config/skills.yaml src/hypo_agent/gateway/app.py src/hypo_agent/core/skill_manager.py tests/gateway/test_app_deps_permissions.py tests/gateway/test_settings.py tests/gateway/test_skill_loading_smoke.py
git commit -m "feat(skills): A2 - unify skill config boundaries"
```

---

### Task A3: Add logical `skill_name` metadata to invocation and CircuitBreaker

**Files:**
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/security/circuit_breaker.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/core/pipeline.py` only if invocation context propagation requires it
- Modify: `tests/security/test_circuit_breaker.py`
- Modify: `tests/skills/test_skill_manager.py`
- Modify: `tests/memory/test_structured_store.py`

**Step 1: Write failing tests**

Add tests for:
- persisted tool invocation includes `skill_name`
- direct/builtin tool path records `"direct"` or other explicit origin
- CircuitBreaker can count by `(tool_name, skill_name)` without regressing existing per-tool/session behavior
- logging fields include `skill_name`

Run:
```bash
pytest -q tests/security/test_circuit_breaker.py tests/skills/test_skill_manager.py tests/memory/test_structured_store.py
```

Expected:
- FAIL because `skill_name` does not exist in persistence or breaker keys

**Step 2: Extend invocation persistence contract**

Minimal schema change:
- add `skill_name` column to `tool_invocations`
- record Python skill name for normal skill tools
- record `"direct"` for builtin/direct tools when no logical skill exists

Migration requirement:
- safe `ALTER TABLE` path or table-bootstrap compatibility for existing DBs/tests

**Step 3: Thread `skill_name` through SkillManager**

When `invoke()` resolves tool dispatch:
- identify owning Python skill name when tool comes from `_tool_to_skill`
- identify builtin/direct source otherwise
- include `skill_name` in:
  - logger fields
  - `_record_tool_invocation(...)`
  - CircuitBreaker hooks

**Step 4: Extend CircuitBreaker**

Preserve current behavior:
- global tool fuse by `(session_id, tool_name)` still works

Add optional logical dimension:
- per `(session_id, tool_name, skill_name)` tracking
- optionally per-skill fuse if config or implementation chooses to always enable it internally

If no new config knob is introduced in this step, implement the new counters but keep current external semantics unchanged except extra observability.

**Step 5: Re-run tests**

Run:
```bash
pytest -q tests/security/test_circuit_breaker.py tests/skills/test_skill_manager.py tests/memory/test_structured_store.py
```

Expected:
- PASS

**Step 6: Full Part A regression**

Run:
```bash
pytest -q
```

Expected:
- PASS before starting Part B

**Step 7: Commit**

```bash
git add src/hypo_agent/core/skill_manager.py src/hypo_agent/security/circuit_breaker.py src/hypo_agent/memory/structured_store.py tests/security/test_circuit_breaker.py tests/skills/test_skill_manager.py tests/memory/test_structured_store.py
git commit -m "feat(skills): A3 - add skill-aware tool invocation metadata"
```

---

### Task B1: Create `skills/` repository structure

**Files:**
- Create: `skills/index.md`
- Create: `skills/pure/.gitkeep` (or first real skill directories if created immediately)
- Create: `skills/hybrid/.gitkeep`
- Create: `skills/internal/.gitkeep`

**Step 1: Add directory structure and placeholder index**

Index content should be human-readable and explicitly marked as non-runtime source of truth.

**Step 2: Add/update tests if directory presence is asserted**

If no tests exist yet, defer coverage to B3 SkillCatalog scan tests.

**Step 3: Commit**

```bash
git add skills/index.md skills/pure skills/hybrid skills/internal
git commit -m "feat(skills): B1 - add skill repository structure"
```

---

### Task B2: Define `SKILL.md` manifest spec

**Files:**
- Create: `skills/SPEC.md`
- Create: `tests/core/test_skill_catalog.py`

**Step 1: Write failing manifest parsing tests**

Add tests that describe a valid frontmatter block containing:
- `name`
- `description`
- `allowed-tools`
- `metadata.hypo.category`
- `metadata.hypo.backend`
- `metadata.hypo.exec_profile`
- `metadata.hypo.triggers`
- `metadata.hypo.risk`
- `metadata.hypo.dependencies`

Expected parser output should normalize:
- `allowed-tools` into list
- triggers/dependencies into list

Run:
```bash
pytest -q tests/core/test_skill_catalog.py -k manifest
```

Expected:
- FAIL because spec/parser do not exist

**Step 2: Write `skills/SPEC.md`**

Document:
- frontmatter schema
- field semantics
- example manifest for pure CLI and hybrid skill
- allowed tool whitelist semantics
- backend ownership semantics

**Step 3: Commit**

```bash
git add skills/SPEC.md tests/core/test_skill_catalog.py
git commit -m "feat(skills): B2 - define SKILL manifest specification"
```

---

### Task B3: Implement SkillCatalog

**Files:**
- Create: `src/hypo_agent/core/skill_catalog.py`
- Modify: `src/hypo_agent/core/__init__.py` if exports are maintained
- Modify: `tests/core/test_skill_catalog.py`
- Optionally create: `tests/fixtures/skills_catalog/` with sample `SKILL.md` + `references/`

**Step 1: Extend failing tests to cover scan/match/lazy load**

Test cases:
- scan discovers all nested `skills/**/SKILL.md`
- invalid manifest is rejected with clear error
- `list_manifests()` returns normalized metadata
- `match_candidates()` hits based on trigger keyword overlap
- `load_body()` returns markdown body without frontmatter
- `load_references()` reads reference files lazily

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
```

Expected:
- FAIL until catalog is implemented

**Step 2: Implement parser and manifest model**

Implement:
- `SkillManifest`
- `SkillCatalog.__init__(skills_dir)`
- `scan()`
- `list_manifests()`
- `match_candidates(user_message)`
- `load_body(skill_name)`
- `load_references(skill_name)`

Implementation notes:
- frontmatter parsing can use `yaml.safe_load` on the header region
- fail closed on malformed frontmatter
- keep body/reference loading lazy and file-system based
- do not entangle SkillCatalog with SkillManager internals yet

**Step 3: Re-run tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py
```

Expected:
- PASS

**Step 4: Commit**

```bash
git add src/hypo_agent/core/skill_catalog.py tests/core/test_skill_catalog.py tests/fixtures/skills_catalog
git commit -m "feat(skills): B3 - implement SkillCatalog manifest scanner"
```

---

### Task B4: Integrate SkillCatalog into Pipeline prompt construction

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/core/test_pipeline.py`
- Modify: `tests/core/test_pipeline_tools.py`
- Modify: `tests/gateway/test_app_deps_permissions.py` or relevant app wiring tests

**Step 1: Write failing tests for candidate skill prompt injection**

Add tests for:
- no catalog configured => prompt identical to previous behavior
- catalog configured but no match => no injected skill section
- matched skill => system prompt contains named `skill_instructions` block with loaded body
- multiple candidates => deterministic ordering and source labels

Run:
```bash
pytest -q tests/core/test_pipeline.py tests/core/test_pipeline_tools.py
```

Expected:
- FAIL on missing `skill_instructions` path

**Step 2: Add optional SkillCatalog dependency to app/pipeline**

Wiring:
- initialize `SkillCatalog` from repo-root `skills/`
- call `scan()` on startup
- pass catalog into `ChatPipeline`

Backward compatibility:
- if catalog init fails or path is missing, log and continue without it

**Step 3: Inject skill instructions during prompt build**

Prompt placement:
- after persona/system/tool rules
- before user/history is acceptable
- label each block with skill name and path

Suggested structure:
```text
[Skill Instructions]
## skill-name
<SKILL body>
```

Keep Python tool schema injection untouched.

**Step 4: Re-run tests**

Run:
```bash
pytest -q tests/core/test_pipeline.py tests/core/test_pipeline_tools.py tests/core/test_skill_catalog.py
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py
git commit -m "feat(skills): B4 - inject SkillCatalog instructions into pipeline"
```

---

### Task C1: Migrate `log_inspector` to pure `SKILL.md`

**Files:**
- Create: `skills/pure/log-inspector/SKILL.md`
- Create if needed: `skills/pure/log-inspector/scripts/*.py` or `.sh`
- Modify: `config/skills.yaml`
- Modify: `tests/skills/test_log_inspector_skill.py` or replace with SkillCatalog-facing tests
- Modify: `tests/core/test_skill_catalog.py`
- Modify: `tests/core/test_pipeline.py` if candidate matching is validated

**Step 1: Write failing migration tests**

Test expectations:
- `log-inspector` manifest scans correctly
- trigger phrases like "最近错误日志" match this skill
- prompt injection contains instructions referencing:
  - `journalctl`
  - `sqlite3` tool history lookup
  - session `.jsonl`
  - grep/jq summarization path
- runtime Python `log_inspector` skill is disabled by config

Run:
```bash
pytest -q tests/core/test_skill_catalog.py tests/core/test_pipeline.py tests/skills/test_log_inspector_skill.py
```

Expected:
- FAIL until manifest exists and config changes

**Step 2: Author the pure skill**

Frontmatter:
- `name: "log-inspector"`
- category `pure`
- backend `exec`
- explicit `allowed-tools`
- `hypo.exec_profile` should likely use a new or existing readonly host/system profile if log commands require `journalctl`, `sqlite3`, `grep`, `jq`

Body must instruct:
- inspect recent logs with bounded `journalctl`
- inspect tool invocation history via SQLite
- inspect session history JSONL
- produce a concise summary without mutating state

**Step 3: Disable old Python skill**

Set:
- `log_inspector.enabled: false`

Do not delete Python implementation yet.

**Step 4: Re-run tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py tests/core/test_pipeline.py tests/skills/test_log_inspector_skill.py
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add skills/pure/log-inspector/SKILL.md config/skills.yaml tests/core/test_skill_catalog.py tests/core/test_pipeline.py tests/skills/test_log_inspector_skill.py
git commit -m "feat(skills): C1 - migrate log inspector to pure skill"
```

---

### Task C2: Create the five pure CLI skills

**Files:**
- Create: `skills/pure/git-workflow/SKILL.md`
- Create: `skills/pure/system-service-ops/SKILL.md`
- Create: `skills/pure/python-project-dev/SKILL.md`
- Create: `skills/pure/hypo-agent-ops/SKILL.md`
- Create: `skills/pure/host-inspection/SKILL.md`
- Modify: `config/exec_profiles.yaml`
- Modify: `skills/index.md`
- Modify: `tests/core/test_skill_catalog.py`
- Modify: `tests/core/test_pipeline.py`

**Step 1: Write failing tests for manifest presence and matching**

Add tests for:
- six manifests total (including `log-inspector`) are discovered
- trigger phrases map to expected skills:
  - git status/commit/diff -> `git-workflow`
  - systemctl/journalctl/service status -> `system-service-ops`
  - pytest/uv/ruff -> `python-project-dev`
  - hypo-agent smoke/restart -> `hypo-agent-ops`
  - df/free/ps/ss -> `host-inspection`
- required exec profiles are present

Run:
```bash
pytest -q tests/core/test_skill_catalog.py tests/core/test_pipeline.py
```

Expected:
- FAIL until manifests and profiles are added

**Step 2: Extend exec profiles**

Add:
- `hypo-agent`
- `host-inspect`
- likely `log-inspect` if `journalctl/sqlite3/grep/jq` should not ride on `systemd`

Ensure deny rules block destructive variants.

**Step 3: Author each skill body**

Each skill must include:
- full frontmatter
- allowed-tools whitelist
- explicit exec profile
- risk level
- dependencies
- trigger keywords
- safety constraints in prose

`git-workflow`:
- status, diff, log-first workflow
- no `reset --hard`, no force push, no clobbering unstaged user changes

`system-service-ops`:
- readonly diagnosis default
- restart requires explicit explanation of impact

`python-project-dev`:
- use `uv sync`, minimal-target `pytest -x`, `ruff check`
- avoid broad full-suite runs by default

`hypo-agent-ops`:
- default to test mode and port `8766`
- production port `8765` only when explicitly requested

`host-inspection`:
- strictly readonly resource inspection
- no process killing or config changes

**Step 4: Update `skills/index.md`**

Generate/update human-readable overview:
- name
- category
- description

**Step 5: Re-run tests**

Run:
```bash
pytest -q tests/core/test_skill_catalog.py tests/core/test_pipeline.py
```

Expected:
- PASS

**Step 6: Final full regression**

Run:
```bash
pytest -q
```

Expected:
- PASS

**Step 7: Commit**

```bash
git add skills/pure/git-workflow/SKILL.md skills/pure/system-service-ops/SKILL.md skills/pure/python-project-dev/SKILL.md skills/pure/hypo-agent-ops/SKILL.md skills/pure/host-inspection/SKILL.md config/exec_profiles.yaml skills/index.md tests/core/test_skill_catalog.py tests/core/test_pipeline.py
git commit -m "feat(skills): C2 - add initial pure CLI skills"
```

---

## Final Verification Checklist

After all commits, run in order:

```bash
pytest -q
```

Optional post-implementation smoke if execution proceeds:

```bash
bash test_run.sh
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

## Risks and Watchouts

- `exec_script` profile enforcement is easy to under-specify; decide clearly whether policy attaches to interpreter command, temporary-file execution, or both.
- `SkillCatalog` prompt injection can silently bloat system prompt; keep candidate matching conservative and deterministic in V1.
- `SkillManager` registration/source logging must not double-register builtin tools or break existing tests that assert exact tool sets.
- `tool_invocations` schema migration can break old tests/fixtures if not implemented defensively.
- Disabling `log_inspector` Python skill means any tests assuming tool schema presence must be updated to assert SkillCatalog injection instead.

## Recommended Execution Order

1. A1
2. A2
3. A3
4. Full `pytest -q`
5. B1
6. B2
7. B3
8. B4
9. C1
10. C2
11. Final `pytest -q`

