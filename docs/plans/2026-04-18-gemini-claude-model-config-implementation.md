# Gemini And Claude Model Config Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add validated Gemini low/high/flash and Claude model definitions without changing default task routing.

**Architecture:** Keep the existing config-driven model router intact and extend only the model catalog plus provider examples. Use TDD to lock the new fallback chains and provider templates, then validate the resulting live model connectivity against the VSPLab Antigravity gateway.

**Tech Stack:** Python, pytest, YAML config, LiteLLM

---

### Task 1: Lock The Expected Repo Config Shape

**Files:**
- Modify: `tests/core/test_config_loader.py`
- Modify: `tests/test_models_serialization.py`
- Test: `tests/core/test_config_loader.py`
- Test: `tests/test_models_serialization.py`

**Step 1: Write the failing test**

Add assertions that the repo `config/models.yaml` exposes:
- `GeminiHigh -> GeminiLow`
- `GeminiLow -> GPT`
- `GeminiFlash -> EdenQwen`
- `Claude` with a provider-backed model definition

Add assertions that `config/secrets.yaml.example` includes placeholder providers for:
- `VSPLab`
- `VSPLab_Gemini`
- `VSPLab_Claude`

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config_loader.py tests/test_models_serialization.py -q`
Expected: FAIL because the repo config and example secrets do not yet contain the new models/providers.

**Step 3: Write minimal implementation**

Update the YAML files only as needed to satisfy the asserted model definitions and example providers.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_config_loader.py tests/test_models_serialization.py -q`
Expected: PASS

### Task 2: Align Local Runtime Secrets With The New Providers

**Files:**
- Modify: `config/secrets.yaml`
- Test: `tests/core/test_config_loader.py`

**Step 1: Write the failing test**

Rely on the repo config loader smoke test reading local `config/secrets.yaml` and requiring provider entries for all referenced models.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config_loader.py::test_repo_models_config_routes_lightweight_and_heartbeat_to_non_coding_models -q`
Expected: FAIL after the new model definitions reference providers missing from local secrets.

**Step 3: Write minimal implementation**

Add provider entries required by the new model definitions to local `config/secrets.yaml`, matching the validated Antigravity endpoint shape.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_config_loader.py::test_repo_models_config_routes_lightweight_and_heartbeat_to_non_coding_models -q`
Expected: PASS

### Task 3: Validate Live Connectivity

**Files:**
- Modify: `config/models.yaml`
- Modify: `config/secrets.yaml.example`
- Modify: `config/secrets.yaml`

**Step 1: Probe live model combinations**

Run LiteLLM probes for:
- `anthropic/gemini-3.1-pro-low`
- `anthropic/gemini-3.1-pro-high`
- `anthropic/gemini-2.5-flash`
- `anthropic/claude-opus-4-5-thinking`

**Step 2: Confirm the correct gateway shape**

Verify that the working Anthropic-compatible base URL is `http://api.vsplab.cn/antigravity`, not `/v1` and not `/v1beta`.

**Step 3: Run final verification**

Run:
- `uv run pytest tests/core/test_config_loader.py tests/test_models_serialization.py -q`
- `uv run python scripts/check_models.py --models GPT,GeminiLow,GeminiHigh,GeminiFlash,Claude --timeout 20`

Expected:
- tests PASS
- GPT, Gemini low/high, and Claude report successful probes when upstream capacity allows
- Gemini Flash may surface upstream rate limits, which should be reported as an environment/provider issue rather than a config parse issue
