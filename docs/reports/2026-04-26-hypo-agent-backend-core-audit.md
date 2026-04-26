# Hypo-Agent Backend Core Audit

Date: 2026-04-26

## Scope

This audit reviews backend foundations for Skills, Memory, ReAct/tooling, circuit breaker behavior, permission handling, and message queue processing. It is read-only and does not modify production code.

## Findings

### High: Circuit breaker treats too many recoverable model/tool errors as execution failures

Evidence:

- `SkillManager.invoke` records any non-success `SkillOutput` as a circuit breaker failure in `src/hypo_agent/core/skill_manager.py:529-540`.
- Unknown tools return `SkillOutput(status="error")` and are recorded as errors in `src/hypo_agent/core/skill_manager.py:388-414`.
- Permission denials return `SkillOutput(status="error")` and are recorded as blocked rows in `src/hypo_agent/core/skill_manager.py:416-450`.
- `CircuitBreaker.record_failure` increments tool, skill, and session failure counters for every recorded failure in `src/hypo_agent/security/circuit_breaker.py:120-168`.
- Runtime history shows `read_file` disabled after 3 failures 18 times, `read_file` session circuit breaker open 5 times, and `notion_query_db` disabled after 3 failures 6 times.

Impact:

The system cannot distinguish between:

- model selected an invalid tool name,
- model omitted a required argument,
- user requested a missing file,
- permission policy correctly denied an unsafe action,
- external service is temporarily unavailable,
- real tool implementation failure.

For small models this is especially harmful: ordinary recoverable mistakes become permanent short-term capability loss.

Recommended direction:

Introduce a typed tool outcome taxonomy before circuit breaker accounting:

- `model_error`: unknown tool, invalid arguments, missing required fields.
- `user_input_error`: file not found, missing query, missing reminder id.
- `policy_block`: permission denied or disallowed operation.
- `external_unavailable`: Notion/web/search timeout or API auth/schema outage.
- `tool_bug`: unhandled exception, invalid output, internal invariant break.
- `dangerous_failure`: command rejected, sandbox failure, security violation.

Only `tool_bug` and `dangerous_failure` should count strongly toward fuse. `external_unavailable` should use service health/backoff. `model_error` should trigger repair guidance and tool schema narrowing.

### High: The single event queue can block later messages behind one long user request

Evidence:

- `EventQueue` is one unbounded `asyncio.Queue` in `src/hypo_agent/core/event_queue.py:15-32`.
- `ChatPipeline.enqueue_user_message` puts user messages into that queue in `src/hypo_agent/core/pipeline.py:531-545`.
- `_consume_event_loop` processes one event at a time and awaits `_consume_user_message_event` before reading the next event in `src/hypo_agent/core/pipeline.py:3680-3706`.
- `_consume_user_message_event` awaits the full `stream_reply` async iterator in `src/hypo_agent/core/pipeline.py:3742-3749`.
- Normal ReAct timeout can be 120 seconds and heartbeat timeout can be 180+ seconds via `src/hypo_agent/core/pipeline.py:642-660`.

Impact:

One stuck LLM/tool call blocks every later queued message, including unrelated user messages and scheduled events. This matches the user report that if one message gets stuck, later messages become unusable.

Recommended direction:

Split queue processing by concern:

- inbound queue accepts events quickly and creates tracked work items,
- per-session executor keeps same-session ordering,
- global semaphore limits concurrent LLM/tool work,
- scheduled events use lower-priority lane,
- every work item has cancellation, timeout, and visible status.

At minimum, `_consume_event_loop` should not await full user-message completion inline; it should spawn a managed per-session task and continue consuming.

### High: Memory taxonomy is not represented in storage or tool API

Evidence:

- `MemorySkill` exposes only `save_preference` and `get_preference` in `src/hypo_agent/skills/memory_skill.py:19-62`.
- `StructuredStore` defines `preferences(pref_key, pref_value, updated_at)` only in `src/hypo_agent/memory/structured_store.py:159-166`.
- `set_preference` is generic key/value upsert in `src/hypo_agent/memory/structured_store.py:565-582`.
- `_preferences_context` injects the latest 20 rows as `[High Priority User Preferences]` in `src/hypo_agent/core/pipeline.py:2741-2771`.
- Runtime rows show auth state, channel state, runtime scan state, personal facts, and behavior rules all in `preferences`.

Impact:

The assistant is told that all recent preference rows are high-priority user preferences even when they are runtime state or auth workflow state. This pollutes prompts and makes memory consolidation unsafe. It also explains why memory is often semantically wrong or not self-organized.

Recommended direction:

Create explicit memory classes:

- `user_profile`: stable user facts and preferences.
- `interaction_policy`: response style and language policy.
- `operational_state`: service cursors, alert timestamps, channel ids.
- `credentials_state`: auth pending/login metadata; should not be injected into LLM prompt by default.
- `knowledge_note`: durable facts or project notes.
- `sop`: approved reusable procedures.

Replace `save_preference` with a planner-style API such as `propose_memory`, `save_memory_item`, `update_memory_item`, `archive_memory_item`, and keep a compatibility adapter for old keys.

### High: Memory consolidation exists but is session-summary-only and not integrated with memory planning

Evidence:

- `MemoryGC.run` iterates old session JSONL files and summarizes them with a lightweight model in `src/hypo_agent/memory/memory_gc.py:48-83`.
- It skips active sessions based on `active_window_days` and minimum message count in `src/hypo_agent/memory/memory_gc.py:94-121`.
- The summary prompt asks for pitfalls, decisions, preference changes, and knowledge points in Chinese in `src/hypo_agent/memory/memory_gc.py:146-176`.
- There is no taxonomy-aware extraction or conflict resolution in the GC flow.

Impact:

There is a useful consolidation hook, but it does not solve the main issue: deciding what kind of memory an item is, whether it should be injected, whether it supersedes older memory, and whether it should be Chinese-first.

Recommended direction:

Turn MemoryGC into a memory maintenance pipeline:

1. Extract candidate memory items from sessions.
2. Classify into taxonomy.
3. Deduplicate and merge with existing memory.
4. Mark language policy; default user-facing memory notes to Chinese unless the source is inherently English.
5. Write a reviewable consolidation report before applying.
6. Index only eligible knowledge/profile/SOP content for semantic retrieval.

### Medium: Tool exposure is better than all-tools, but the core whitelist is still broad and English-heavy

Evidence:

- Core tool whitelist includes execution, file IO, reminders, memory, SOP, and search tools in `src/hypo_agent/core/pipeline.py:180-199`.
- The skill catalog tells the model "直接尝试调用" in `src/hypo_agent/core/skill_manager.py:151-173`.
- Progressive disclosure dynamically loads unexposed tool owners in `src/hypo_agent/core/pipeline.py:864-929` and `src/hypo_agent/core/pipeline.py:1720-1812`.
- Tool descriptions and many error strings are English or low-context, for example `query is required`, `Unsupported tool`, `Command exited with status 1`.

Impact:

Progressive disclosure reduces prompt bloat, but small models still receive many actions that are easy to misuse. The guidance encourages trying tools without teaching recovery or safe planning.

Recommended direction:

Add a tool selection layer before actual tool calls:

- expose smaller domain-specific bundles,
- provide examples and "before calling" constraints,
- require a brief tool plan for high-risk tools,
- translate common errors into Chinese user/model-facing repair hints,
- store per-tool failure guides in skill metadata.

### Medium: Retrying happens after circuit breaker accounting, so retryable tool errors can accelerate fusion

Evidence:

- `_invoke_tool_with_retry` allows two attempts for selected retryable tools in `src/hypo_agent/core/pipeline.py:3412-3480`.
- Each attempt calls `skill_manager.invoke`, which records failure and updates circuit breaker before the pipeline decides to retry in `src/hypo_agent/core/skill_manager.py:529-540`.

Impact:

A transient `web_search` timeout or `read_file` temporary issue can consume multiple breaker failure counts within one user request. Retrying should be coordinated with the breaker so only the final classified outcome affects fuse state.

Recommended direction:

Move retry policy below or inside the breaker accounting boundary:

- classify output,
- decide retry/backoff,
- only record final failed classification,
- optionally record attempts as observability rows without breaker impact.

### Medium: Permission policy and operation inference can misclassify tool behavior

Evidence:

- `SkillManager._infer_operation` treats `update_*` as write and `run_*` as execute in `src/hypo_agent/core/skill_manager.py:572-581`.
- `_OPERATION_OVERRIDES` declares `scan_directory` as read in `src/hypo_agent/core/skill_manager.py:23-31`.
- Permission manager enforces longest-prefix whitelist and read-only default in `src/hypo_agent/security/permission_manager.py:72-123`.
- Runtime data shows `scan_directory` denied as a write operation despite the override, suggesting another tool path or manifest/logical skill path uses mismatched tool naming or parameters.

Impact:

Small inconsistencies in operation inference lead to policy-block errors that look like tool failures. These then feed the breaker and confuse the assistant.

Recommended direction:

Move operation classification into each tool schema/manifest rather than inferring from names. Persist `operation`, `failure_class`, and `safe_to_retry` in `tool_invocations`.

### Medium: Notion tool API is too raw for small-model use

Evidence:

- Notion tool descriptions expose raw `properties`, `filter`, and `sorts` JSON in `src/hypo_agent/skills/notion_skill.py:132-173`.
- Runtime failures include missing property names (`Status`, `Date`, `Created time`, `Name`) and body validation errors.
- `notion_get_schema` exists, but the model is not forced through schema-first planning before querying/updating.

Impact:

The model has to infer a remote database schema from memory and often guesses wrong. The tool should prevent invalid property names or offer a schema-bound query builder.

Recommended direction:

Split Notion into safe high-level tools:

- `notion_find_database`
- `notion_query_by_schema`
- `notion_update_todo`
- `notion_create_todo`
- `notion_schema_cache_refresh`

For raw JSON tools, require a prior schema id/version and validate locally before remote calls.

## Reproducible Failure/Test Designs

1. **Breaker should not fuse on unknown tool names**
   - Build a pipeline with a fake router that calls `execread_file`.
   - Assert the response gives a correction hint and `CircuitBreaker` counters do not fuse `read_file`.

2. **Permission denial should be `policy_block`, not generic tool failure**
   - Invoke `read_file` or `write_file` against blocked/out-of-whitelist paths.
   - Assert `tool_invocations.failure_class == policy_block` and breaker does not count it as a tool implementation failure.

3. **Single queue blocking reproduction**
   - Put a `user_message` whose fake router stream sleeps for several seconds.
   - Immediately put a second user message with another session id.
   - Assert current code does not emit the second message until the first completes; target behavior should allow it under per-session concurrency.

4. **Memory injection pollution**
   - Insert `auth.pending.weibo` and `timezone` into preferences.
   - Build LLM messages.
   - Assert current code injects both as high-priority preferences; target behavior should inject only user-facing memory.

5. **Retry should not double-count breaker failure**
   - Fake `web_search` timeout on first attempt, success on second.
   - Assert current code records first failure into breaker; target behavior should record attempt telemetry but no breaker failure after final success.

## Recommended Ownership Split

Keep Skills for:

- deterministic local file read/list/write with permission policy,
- reminders,
- memory management after taxonomy redesign,
- log inspection,
- safe search wrappers with clear timeout/health behavior.

Move toward dedicated services or Codex SDK lane for:

- repository code edits, multi-step file operations, repair tasks,
- complex command-line workflows,
- long-running diagnostics,
- Notion database manipulation that needs schema planning,
- operations where natural-language tool choreography has repeatedly failed.

The Codex SDK lane should expose a small number of conversational operations such as "inspect repo", "apply patch", "run verification", "summarize diff", backed by task tracking and cancellation.

## Backend Refactor Dependency Graph

```text
M1 failure taxonomy + invocation schema
  -> M2 breaker accounting rewrite
  -> M3 tool guidance and small-model repair hints

M4 memory taxonomy + migration adapter
  -> M5 memory planner API
  -> M6 async consolidation and Chinese-first summaries

M7 managed work queue + per-session executors
  -> M8 cancellation/status UI contract
  -> M9 WebUI responsive message handling

M10 skill acceptance probes
  -> M11 retire or replace fragile skills
  -> M12 Codex SDK execution lane
```

## Validation

Validation mode: inline code review.

The review references concrete files and line ranges and proposes reproducible test designs. No backend business code was modified.

## Next

Proceed to M3: audit skill acceptance paths and WebUI usability/performance.
