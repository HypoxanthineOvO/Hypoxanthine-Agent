# M0: Project Skeleton & Config Framework Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Hypo-Agent M0 foundation with project skeleton, config scaffolding, core Pydantic v2 models, baseline test setup, and logging initialization.

**Architecture:** Use `src/` package layout with import root `hypo_agent`. Keep M0 intentionally minimal: schema-first configs and data models without runtime service orchestration. Enforce TDD on all Python behavior changes (models/logging utility), while creating config and project metadata as static scaffolding.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, structlog, pytest, PyYAML, LiteLLM, SQLite

---

### Task 1: Create Base Repository Skeleton

**Files:**
- Create: `config/models.yaml`
- Create: `config/skills.yaml`
- Create: `config/security.yaml`
- Create: `config/tasks.yaml`
- Create: `config/persona.yaml`
- Create: `src/hypo_agent/__init__.py`
- Create: `src/hypo_agent/gateway/__init__.py`
- Create: `src/hypo_agent/core/__init__.py`
- Create: `src/hypo_agent/memory/__init__.py`
- Create: `src/hypo_agent/skills/__init__.py`
- Create: `src/hypo_agent/scheduler/__init__.py`
- Create: `src/hypo_agent/security/__init__.py`
- Create: `workflows/.gitkeep`
- Create: `memory/sessions/.gitkeep`
- Create: `memory/knowledge/.gitkeep`
- Create: `memory/hypo.db`
- Create: `web/.gitkeep`
- Create: `tests/__init__.py`
- Create: `logs/.gitkeep`

**Step 1: Initialize repository**
Run: `git init`
Expected: repository initialized

**Step 2: Create directory tree**
Run: `mkdir -p config workflows memory/sessions memory/knowledge src/hypo_agent/{gateway,core,memory,skills,scheduler,security} web tests logs`
Expected: directories created without errors

**Step 3: Create package markers and keep files**
Run: `touch src/hypo_agent/__init__.py src/hypo_agent/gateway/__init__.py src/hypo_agent/core/__init__.py src/hypo_agent/memory/__init__.py src/hypo_agent/skills/__init__.py src/hypo_agent/scheduler/__init__.py src/hypo_agent/security/__init__.py workflows/.gitkeep memory/sessions/.gitkeep memory/knowledge/.gitkeep web/.gitkeep tests/__init__.py logs/.gitkeep memory/hypo.db`
Expected: files created

**Step 4: Commit scaffold**
```bash
git add workflows memory src web tests logs
git commit -m "chore: scaffold m0 project directory structure"
```

### Task 2: Add Project Metadata and Environment Definition

**Files:**
- Create: `pyproject.toml`
- Create: `environment.yml`

**Step 1: Write metadata files**
- `pyproject.toml`: project name `hypo-agent`, package discovery from `src`, runtime deps (`fastapi`, `uvicorn`, `pydantic`, `pyyaml`, `structlog`, `litellm`), test deps (`pytest`, `pytest-cov`)
- `pyproject.toml`: include pytest config section early
  - `testpaths = ["tests"]`
  - `pythonpath = ["src"]`
  - `addopts = "-ra -q"`
- `environment.yml`: conda env with Python 3.12 + pip install of project

**Step 2: Validate TOML parseability**
Run: `python -c "import tomllib;print('ok')" < /dev/null`
Expected: `ok`

**Step 3: Commit metadata**
```bash
git add pyproject.toml environment.yml
git commit -m "build: add pyproject metadata and conda environment"
```

### Task 3: Add YAML Config Skeletons with Safe Defaults

**Files:**
- Modify: `config/models.yaml`
- Modify: `config/skills.yaml`
- Modify: `config/security.yaml`
- Modify: `config/tasks.yaml`
- Modify: `config/persona.yaml`

**Step 1: Add minimal but valid YAML defaults**
- `models.yaml`: default model + task-to-model mapping
- `skills.yaml`: skill enablement and timeout defaults
- `security.yaml`: read/write/execute whitelist and circuit breaker threshold defaults
- `tasks.yaml`: scheduler defaults and retry policy
- `persona.yaml`: assistant name/aliases/personality/style

**Step 2: Validate YAML syntax**
Run: `python -c "import yaml,glob; [yaml.safe_load(open(p)) for p in glob.glob('config/*.yaml')]; print('yaml ok')"`
Expected: `yaml ok`

**Step 3: Commit config skeleton**
```bash
git add config/*.yaml
git commit -m "chore: add m0 yaml configuration skeletons"
```

### Task 4: RED - Write Failing Tests for Data Models and Logging Config

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_models_serialization.py`

**Step 1: Write failing tests for model serialization/deserialization**
Include tests for:
- `Message` round-trip with datetime and optional multimodal fields
- `SkillOutput` status enum validation
- `ModelConfig` task mapping and defaults
- `SecurityConfig` whitelist shape and circuit breaker values
- `PersonaConfig` required identity/style fields
- `configure_logging()` initializes structlog processors and logger usability

**Step 2: Run tests to verify RED**
Run: `pytest -q tests/test_models_serialization.py`
Expected: FAIL due to missing `src/hypo_agent/models.py` and missing logging setup

**Step 3: Commit red tests**
```bash
git add tests/conftest.py tests/test_models_serialization.py
git commit -m "test: add failing serialization tests for m0 models"
```

### Task 5: GREEN - Implement Pydantic Models

**Files:**
- Create: `src/hypo_agent/models.py`

**Step 1: Implement minimal code to pass tests**
Implement:
- `Message`
- `SkillOutput` (status as Literal of `success|error|partial|timeout`)
- `ModelConfig`
- `SecurityConfig`
- `PersonaConfig`
Use Pydantic v2 `BaseModel`, defaults matching YAML skeleton.

**Step 2: Run focused tests**
Run: `pytest -q tests/test_models_serialization.py`
Expected: all tests PASS

**Step 3: Commit GREEN implementation**
```bash
git add src/hypo_agent/models.py
git commit -m "feat: implement core pydantic models for m0"
```

### Task 6: Add Structlog Initialization Utility

**Files:**
- Create: `src/hypo_agent/core/logging.py`

**Step 1: Implement `configure_logging()` with sane defaults**
- JSON rendering for production readiness
- timestamp, level, logger name processors
- idempotent safe config for repeated calls

**Step 2: Run test suite**
Run: `pytest -q tests/test_models_serialization.py`
Expected: PASS including logging-related assertion

**Step 3: Commit logging config**
```bash
git add src/hypo_agent/core/logging.py
git commit -m "feat: add structlog initialization for m0"
```

### Task 7: Add Pytest and Tooling Config

**Files:**
- Verify: `pyproject.toml`

**Step 1: Verify pytest config is active**
Run: `pytest --help | rg -n \"testpaths|pythonpath|addopts\"`
Expected: pytest options resolve with project config loaded

**Step 2: Run full test check**
Run: `pytest`
Expected: PASS

**Step 3: Commit tooling verification**
```bash
git add pyproject.toml
git commit -m "test: verify pytest defaults for src layout"
```

### Task 8: REFACTOR and Final Verification

**Files:**
- Modify: `tests/test_models_serialization.py` (if cleanup needed)
- Modify: `src/hypo_agent/models.py` (if cleanup needed)
- Modify: `src/hypo_agent/core/logging.py` (if cleanup needed)

**Step 1: Refactor without behavior change**
- remove duplication in tests
- improve field descriptions/types only if tests remain green

**Step 2: Final verification commands**
Run:
- `pytest -q`
- `python -c "from hypo_agent.models import Message; print(Message(sender='user', session_id='s1').model_dump())"`
Expected: all checks pass

**Step 3: Final commit**
```bash
git add .
git commit -m "chore: complete m0 skeleton and config framework"
```
