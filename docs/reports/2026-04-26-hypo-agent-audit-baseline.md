# Hypo-Agent Audit Baseline

Date: 2026-04-26

## Scope

This report is the read-only baseline for the full Hypo-Agent audit. It uses local repository files, runtime SQLite history, logs, session files, and test inventory. It does not change production code and does not contact deployed services.

## Repository Baseline

Hypo-Agent is a Python 3.12 FastAPI backend with a Vue 3/Vite WebUI. Key runtime areas are:

- Backend entrypoints: `src/hypo_agent/__main__.py`, `src/hypo_agent/gateway/main.py`, `src/hypo_agent/gateway/app.py`
- Core orchestration: `src/hypo_agent/core/pipeline.py`, `model_router.py`, `skill_manager.py`, `event_queue.py`
- Runtime memory: `src/hypo_agent/memory/`, persisted under `memory/` or `test/sandbox/` in test mode
- Runtime skills: `src/hypo_agent/skills/`
- WebUI: `web/src/`
- Default test-mode acceptance: `bash test_run.sh` and `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`

The generated Hypo-Workflow architecture baseline is under `.pipeline/architecture/`.

## Runtime Database Evidence

Tables found in `memory/hypo.db` include `tool_invocations`, `preferences`, `token_usage`, `sessions`, `reminders`, `processed_emails`, `semantic_chunks`, `semantic_chunks_fts`, `semantic_chunks_vec`, `repair_runs`, and subscription-related tables.

`tool_invocations` status counts:

| Status | Count |
| --- | ---: |
| success | 4970 |
| error | 240 |
| blocked | 29 |
| fused | 22 |
| timeout | 8 |

High-volume tools by status:

| Tool | Status | Count |
| --- | --- | ---: |
| `run_command` | success | 1982 |
| `scan_emails` | success | 484 |
| `list_reminders` | success | 473 |
| `read_file` | success | 408 |
| `exec_command` | success | 320 |
| `get_notion_todo_snapshot` | success | 320 |
| `run_command` | error | 60 |
| `notion_query_db` | error | 56 |
| `read_file` | error | 49 |
| `read_file` | blocked | 15 |

Frequent failure signatures:

| Tool | Error signature | Count |
| --- | --- | ---: |
| `run_command` | `Command exited with status 1` | 26 |
| `notion_query_db` | `'APIResponseError' object has no attribute 'message'` | 21 |
| `read_file` | disabled after 3 consecutive failures | 18 |
| `scan_directory` | write operation not allowed for repo whitelist | 13 |
| `exec_command` | `Command exited with status 1` | 12 |
| `run_command` | path outside whitelist | 8 |
| `notion_query_db` | missing Notion property names | 6+ |
| `execread_file` | unknown tool | 5 |
| `read_file` | session circuit breaker open for `main` | 5 |
| `web_search` | request timed out after 60 seconds | 3 |

Baseline interpretation: the user-reported tool-call instability is visible in runtime data. The dominant failures are not a single bug class; they include invalid tool names, missing parameters, permission mismatch, external API schema mismatch, external service timeout, and circuit breaker escalation.

## Memory Evidence

`preferences` has 15 rows. Recent examples show mixed categories in one table:

| Category | Example key |
| --- | --- |
| Runtime state | `email_scanner.last_heartbeat_scan_started_at`, `cookie_alert_last_weibo` |
| Channel/auth state | `qq_bot.last_openid`, `feishu.last_chat_id.main`, `auth.pending.weibo` |
| External service state | `wewe_rss.last_login_started_at`, `notion.todo_database_id` |
| Personal fact | `campus_card_location`, `travel_time_school_to_hongqiao` |
| Memory/process rule | `memory_file_organization_rule`, `email_lookup_rule_shanghaitech_default` |
| User preference | `timezone` |

Baseline interpretation: the current L2 preference store is acting as a general key-value dump. It is not only user preference memory. This makes autonomous memory planning and periodic cleanup harder because runtime state, secrets-adjacent auth state, facts, and behavior rules have no explicit taxonomy.

## Token Usage Evidence

Top `token_usage` model pairs:

| Requested | Resolved | Calls | Total tokens |
| --- | --- | ---: | ---: |
| `GPT` | `GPT` | 1961 | 36,430,011 |
| `GPT` | `KimiK25` | 481 | 8,197,225 |
| `GenesiQWen35BA3B` | `GenesiQWen35BA3B` | 306 | 4,503,342 |
| `KimiK25` | `KimiK25` | 259 | 812,417 |
| `DeepseekV3_2` | `DeepseekV3_2` | 172 | 668,518 |

Baseline interpretation: the system is already operating across multiple model classes, including smaller/local models. Tool guidance and error recovery need to be small-model-friendly rather than assuming frontier-model repair ability.

## Session And Log Evidence

Session files found under `memory/sessions` and `test/sandbox/memory/sessions`: 61.

Largest session files:

| File | Size |
| --- | ---: |
| `memory/sessions/main.jsonl` | 2,042,300 bytes |
| `test/sandbox/memory/sessions/repair-live-1.jsonl` | 166,308 bytes |
| `test/sandbox/memory/sessions/main.jsonl` | 74,737 bytes |

Log and session evidence includes:

- `logs/backend.log` shows ReAct sessions with `max_rounds=8` and `react_timeout_seconds=120`.
- Heartbeat examples use longer windows such as `timeout_seconds=235`.
- `logs/backend.log` contains a bind failure on `127.0.0.1:8765`, indicating local process/port collision risk.
- A repair session records a false "cannot access" reply after a successful tool call, caused by sanitization only covering one model-response path.

Baseline interpretation: large session history and long ReAct/heartbeat windows can amplify WebUI cost and message queue blocking. There is prior evidence of model/tool mismatch leading to false failure replies.

## Test Inventory

Backend test inventory:

- `test_*.py` files: 145
- Python files under `tests/`: 153
- Many tests intentionally use fakes/mocks to avoid external side effects, including fake QQ/Weixin/IMAP clients, `httpx.MockTransport`, `DummyPipeline`, and monkeypatches.

This is good for safety, but it creates a second audit question: which skills have true test-mode acceptance probes and which only have isolated unit tests.

## Initial Risk Register

| Risk | Severity | Evidence | Next audit target |
| --- | --- | --- | --- |
| Tool errors collapse into circuit breaker outcomes instead of guided recovery | High | `read_file` fused/blocked history; `notion_query_db` fused history | M2 |
| Memory taxonomy is polluted | High | `preferences` mixes runtime, auth, facts, rules, preferences | M2 |
| Single consumer can block queued messages | High | `EventQueue` single queue and pipeline consumer awaits full user-message processing | M2 |
| Skill tests may not prove deployability | High | heavy fake/mock footprint; real runtime failures despite many passing tests | M3 |
| WebUI likely re-renders expensive markdown during streaming | Medium | `TextMessage.vue` renders markdown in template and post-processes text changes | M3 |
| External integrations lack schema adaptation resilience | Medium | Notion property-name and APIResponseError failures | M2/M3 |
| Port/process collisions can confuse local validation | Medium | backend log bind failure on `8765` | M3 |

## Validation

Validation mode: inline, read-only audit validation.

Validated:

- `.pipeline/config.yaml`, `.pipeline/state.yaml`, `.plan-state/*.yaml` parse as YAML.
- `.pipeline/config.yaml` validates against Hypo-Workflow schema.
- SQLite queries ran successfully against `memory/hypo.db`.
- Test inventory and log scans were read-only.

No production smoke was run in this milestone. Production/deployed acceptance remains out of scope unless explicitly requested.

## Next

Proceed to M2: backend review of Skills, Memory, ReAct/tooling, circuit breaker, and message processing.
