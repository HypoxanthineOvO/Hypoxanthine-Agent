# WeWe RSS Watchdog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add WeWe RSS account watchdog, cross-channel invalid-session alerts, and current-channel QR login recovery.

**Architecture:** Introduce a dedicated WeWe client plus a monitor service, wire it into existing scheduler and event queue infrastructure, and add a deterministic pre-LLM shortcut for QR requests so the current-channel reply behavior does not depend on model choice.

**Tech Stack:** Python, FastAPI app lifecycle, APScheduler, Pydantic, qrcode, existing Message/EventQueue pipeline, pytest

---

### Task 1: Add Config Models

**Files:**
- Modify: `src/hypo_agent/models.py`
- Modify: `config/secrets.yaml.example`
- Modify: `config/tasks.yaml`
- Test: `tests/test_models_serialization.py`

**Step 1: Write the failing test**

Add serialization tests covering:
- `services.wewe_rss` parses from secrets YAML
- `tasks.wewe_rss` parses from tasks YAML

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models_serialization.py -k wewe -q`
Expected: FAIL because model fields do not exist.

**Step 3: Write minimal implementation**

Add `WeWeRSSServiceConfig` and a `wewe_rss` schedule config entry.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models_serialization.py -k wewe -q`
Expected: PASS

### Task 2: Add WeWe Client

**Files:**
- Create: `src/hypo_agent/channels/info/wewe_rss_client.py`
- Test: `tests/channels/test_wewe_rss_client.py`

**Step 1: Write the failing test**

Cover:
- auth header injection
- `list_accounts()` parses query response
- `create_login_url()` and `get_login_result()` parse mutation/query response
- 401 raises a dedicated auth error

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/test_wewe_rss_client.py -q`
Expected: FAIL because client module does not exist.

**Step 3: Write minimal implementation**

Implement an HTTP client with small typed helpers around WeWe tRPC endpoints.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/channels/test_wewe_rss_client.py -q`
Expected: PASS

### Task 3: Add Watchdog Service

**Files:**
- Create: `src/hypo_agent/core/wewe_rss_monitor.py`
- Test: `tests/core/test_wewe_rss_monitor.py`

**Step 1: Write the failing test**

Cover:
- invalid account produces alert event
- healthy account clears dedupe state
- QR request generates image attachment
- login success adds account and returns recovery message
- login timeout/failure returns explicit text

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_wewe_rss_monitor.py -q`
Expected: FAIL because service module does not exist.

**Step 3: Write minimal implementation**

Implement monitor service using existing `StructuredStore`, `EventQueue`, and `Message` models.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_wewe_rss_monitor.py -q`
Expected: PASS

### Task 4: Wire Pipeline Shortcut

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/core/test_pipeline.py`

**Step 1: Write the failing test**

Add a test where inbound text like `我扫码登录一下` triggers:
- no LLM call
- outbound attachment image present
- `metadata.target_channels` limited to inbound channel

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pipeline.py -k wewe -q`
Expected: FAIL because shortcut is missing.

**Step 3: Write minimal implementation**

Teach pre-LLM handling to call monitor service directly and return/broadcast a prepared `Message`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_pipeline.py -k wewe -q`
Expected: PASS

### Task 5: Wire App Startup and Scheduler

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/core/event_queue.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/gateway/test_app_scheduler_lifecycle.py`
- Test: `tests/core/test_pipeline_event_consumer.py`

**Step 1: Write the failing test**

Cover:
- app creates monitor service when config is enabled
- scheduler registers WeWe interval/cron job
- WeWe proactive event is converted to `Message`

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_pipeline_event_consumer.py -k wewe -q`
Expected: FAIL because startup and event mapping are missing.

**Step 3: Write minimal implementation**

Instantiate the service from config, register the scheduled check job, and add an event-to-message mapping for WeWe alerts.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_pipeline_event_consumer.py -k wewe -q`
Expected: PASS

### Task 6: Verify End-to-End

**Files:**
- Modify: `README.md` or docs only if needed for operator setup

**Step 1: Run targeted suite**

Run:
- `uv run pytest tests/channels/test_wewe_rss_client.py tests/core/test_wewe_rss_monitor.py tests/core/test_pipeline.py -k wewe -q`
- `uv run pytest tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_pipeline_event_consumer.py -k wewe -q`

Expected: all PASS

**Step 2: Run default app smoke path**

Run:
- `bash test_run.sh`
- `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`

Expected: existing default smoke remains green.

**Step 3: Document operator prerequisites**

Record that production enablement requires a valid `services.wewe_rss.auth_code`.
