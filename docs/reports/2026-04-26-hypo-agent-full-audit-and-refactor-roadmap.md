# Hypo-Agent Full Audit And Refactor Roadmap

Date: 2026-04-26
Input reports:

- `docs/reports/2026-04-26-hypo-agent-audit-baseline.md`
- `docs/reports/2026-04-26-hypo-agent-backend-core-audit.md`
- `docs/reports/2026-04-26-hypo-agent-skill-webui-audit.md`

## Overall Conclusion

Hypo-Agent has useful foundations: a real skill registry, permission controls, memory persistence, WebSocket UI, smoke-mode safeguards, and a broad test suite. The main reliability problem is that several subsystems blur different concepts into one channel:

- model mistakes, user input mistakes, policy blocks, external outages, and tool bugs all become generic tool failures;
- raw tool traces and user-facing conversation share history space;
- user preference, runtime cursor state, auth workflow state, rules, and facts share one `preferences` table;
- one event queue awaits full request completion before consuming later work;
- unit tests and mocked skill tests are treated as stronger evidence than they are.

The first refactor should therefore focus on typed boundaries and observability, not on adding more prompts.

## Ranked Findings

| Priority | Finding | User Impact | Dependency |
| --- | --- | --- | --- |
| P0 | Tool outcome taxonomy is missing; breaker fuses recoverable failures | Small models lose capabilities after ordinary mistakes | Must precede skill guidance and acceptance work |
| P0 | Single queue blocks later messages behind one long request | One stuck request makes the assistant feel dead | Can be implemented after trace/job model exists |
| P0 | Memory taxonomy is absent; `preferences` is polluted | Wrong memories, poor recall, English/default preference drift | Needs schema migration and compatibility adapter |
| P1 | Skill tests do not prove deployability | "Passed" skills fail in real use | Depends on tool taxonomy and deterministic probes |
| P1 | WebUI streaming rerenders expensive markdown per chunk | Chat becomes sluggish on long streams/history | Can proceed in parallel with backend refactors |
| P1 | Tool event history pollutes chat and model context | Small models see noisy mechanical history | Depends on trace/history separation |
| P2 | Notion/raw external tools are too schema-sensitive | Frequent property-name/API validation failures | Depends on skill contracts and schema cache |
| P2 | Memory UI exposes raw tables instead of memory semantics | Operators cannot manage memory quality | Depends on memory taxonomy |

## Skills Strategy

### Recommended Hybrid Path

Keep the Skills system for deterministic, bounded, low-latency operations:

- file read/list/write under permission policy;
- reminders and scheduler-facing operations;
- typed memory operations;
- log inspection and status probes;
- simple service snapshots.

Move high-friction or long-running work into a Codex SDK execution lane:

- repository inspection and code edits;
- multi-step command workflows;
- repair sessions and patch application;
- large diagnostics;
- external integrations that need schema planning and iterative validation.

Expose the Codex SDK lane as a small set of conversational capabilities, not raw shell access: `inspect_repo`, `apply_patch_task`, `run_verification`, `summarize_diff`, `diagnose_failure`. It should produce structured trace events, support cancellation, and obey the same test-mode/production boundary rules.

### Conservative Alternative

Keep all current skills, but add manifests, typed outcomes, schema-bound validators, and acceptance probes. This is lower-risk for compatibility but slower to make code/command workflows robust.

### More Aggressive Alternative

Shrink runtime Skills to memory/reminders/search/status and route most repo, command, and repair work through Codex SDK. This reduces LLM tool choreography but requires a new job subsystem and stronger sandbox/task isolation.

Recommended first phase: implement the conservative foundations while designing the Codex SDK lane. Defer broad migration until outcome taxonomy, traces, cancellation, and probes are in place.

## Target Architectures

### Tooling And Skills

- Every tool invocation stores `outcome_class`, `retryable`, `breaker_weight`, `side_effect_class`, `operation`, `trace_id`, and `user_visible_summary`.
- Circuit breaker counts only weighted final outcomes. `model_error`, `user_input_error`, and `policy_block` should not fuse a tool.
- Skill manifests define schema, operation class, config requirements, side-effect class, timeout budget, retry policy, Chinese repair hints, and acceptance probes.
- Raw external tools are wrapped by safer high-level tools. Notion should use schema cache and local validation before API calls.

### Memory

Use typed memory records:

- `user_profile`: durable user facts and stable preferences.
- `interaction_policy`: language, tone, formatting, and behavior rules.
- `operational_state`: service cursors, timestamps, channel ids, non-user preference runtime values.
- `credentials_state`: auth/login workflow metadata; not injected into prompts by default.
- `knowledge_note`: durable project or world notes.
- `sop`: approved reusable procedures.

Memory API:

- `propose_memory`: model proposes a candidate with reason, class, confidence, source, and language.
- `save_memory_item`, `update_memory_item`, `archive_memory_item`.
- `list_memory_items` by class/status.
- Compatibility adapter maps legacy `preferences` keys into typed views.

Language policy:

- Chinese-first for user-facing memory summaries and interaction rules.
- Preserve English only when the source text, code identifier, paper title, API name, or quoted content requires it.

Consolidation:

- Async scheduled job extracts candidates from sessions, classifies, deduplicates, writes review reports, then applies safe updates.
- Prompt injection should include only eligible memory classes, with provenance and freshness.

### Message Processing

- Inbound event queue should enqueue quickly and create tracked work items.
- Per-session executor preserves ordering inside one session.
- Global semaphore limits total LLM/tool jobs.
- Scheduled/proactive events use a lower-priority lane.
- Every work item has timeout, cancellation, status, and trace id.
- Stuck work should not block unrelated sessions.

### WebUI

- Buffer assistant chunks and commit at a controlled rate.
- Render markdown from a cached computed value keyed by message id and version.
- Defer KaTeX/Mermaid until stream completion or closed block detection.
- Virtualize chat rows and tool history.
- Keep raw tool trace behind a debug panel; default chat history should show summaries and failures that require user action.
- Memory UI should become semantic first; raw SQLite stays as admin/debug.

## Proposed Refactor Milestones

### R1: Outcome Taxonomy, Trace Schema, And Breaker Accounting

Goal: stop fusing recoverable mistakes and make failures diagnosable.

Modify:

- `src/hypo_agent/core/skill_manager.py`
- `src/hypo_agent/security/circuit_breaker.py`
- `src/hypo_agent/memory/structured_store.py`
- tests around skill invocation, permission denials, retries.

Tests:

- Unknown tool name does not fuse any real tool.
- Permission denial records `policy_block` and breaker weight 0.
- Retry success records attempt telemetry but no breaker failure.
- Existing smoke still runs in `HYPO_TEST_MODE=1`.

Compatibility/migration:

- Add nullable columns or a new trace table; keep old `status` for old readers.

Docs:

- Required `R1[doc]`: outcome taxonomy and trace schema.

### R2: Skill Contracts And Acceptance Probes

Goal: make "skill passes" mean something operational.

Modify:

- skill manifests/catalog;
- Notion, memory, exec, filesystem, browser/auth tests;
- smoke/probe scripts.

Tests:

- Per-skill unit gate.
- Contract gate with mocked external API validation.
- Test-mode probe gate that cannot touch real QQ or production port.
- Optional integration marks for real/sandbox services.

Compatibility/migration:

- Existing skills keep old APIs but gain metadata; raw dangerous tools may become debug-only.

Docs:

- Required `R2[doc]`: skill acceptance policy.

### R3: Typed Memory Store And Legacy Preference Migration

Goal: separate user memory from runtime state and add autonomous memory planning.

Modify:

- `src/hypo_agent/memory/structured_store.py`
- `src/hypo_agent/skills/memory_skill.py`
- memory prompt injection in `src/hypo_agent/core/pipeline.py`
- migration scripts/tests.

Tests:

- Legacy keys map into typed classes.
- Auth/runtime keys are not injected as user preferences.
- Chinese-first memory candidate is saved and retrieved.
- Conflict/archive flow works.

Compatibility/migration:

- Back up `preferences`; keep read adapter; migrate gradually.

Docs:

- Required `R3[doc]`: memory taxonomy and migration plan.

### R4: Async Memory Consolidation Pipeline

Goal: add periodic memory cleanup without blocking conversations.

Modify:

- `src/hypo_agent/memory/memory_gc.py`
- scheduler registration;
- review report output;
- Memory UI/API for pending candidates.

Tests:

- Session extraction creates typed candidates.
- Duplicate candidates merge.
- Review-only mode does not mutate memory.
- Apply mode writes audited changes.

Compatibility/migration:

- Default to review-only until confidence is established.

Docs:

- Required `R4[doc]`: consolidation lifecycle and operator workflow.

### R5: Non-Blocking Message Runtime

Goal: prevent one stuck request from blocking later messages.

Modify:

- `src/hypo_agent/core/event_queue.py`
- `src/hypo_agent/core/pipeline.py`
- gateway/WebSocket status events.

Tests:

- Slow session A does not block session B.
- Same-session messages remain ordered or visibly queued.
- Cancellation emits terminal status.
- Scheduled events respect lower priority and global concurrency.

Compatibility/migration:

- Preserve old event payloads; add new job/status events incrementally.

Docs:

- Required `R5[doc]`: queue/job model and cancellation semantics.

### R6: WebUI Performance And Semantic Memory UI

Goal: make long sessions usable and expose memory as meaningful state.

Modify:

- `web/src/composables/useChatSocket.ts`
- `web/src/views/ChatView.vue`
- `web/src/components/chat/*`
- `web/src/views/MemoryView.vue`

Tests:

- Streamed long markdown test.
- Tool-event burst test.
- Long-session virtualization test.
- Memory typed CRUD UI tests.
- Playwright screenshot/performance smoke in test mode.

Compatibility/migration:

- Keep raw trace/table views behind admin/debug tabs.

Docs:

- Required `R6[doc]`: WebUI performance and memory operation notes.

### R7: Codex SDK Execution Lane

Goal: move complex repo/command workflows out of fragile natural-language tool choreography.

Modify:

- new execution service/job API;
- limited WebUI job panel;
- skill adapter that exposes high-level Codex actions.

Tests:

- Inspect/apply/verify jobs run in test workspace.
- Cancellation and timeout work.
- Job trace integrates with structured trace store.
- No production side effects in test mode.

Compatibility/migration:

- Start opt-in. Keep old exec/file skills until the SDK lane proves stable.

Docs:

- Required `R7[doc]`: Codex SDK lane contract and safety model.

## Deprecated Or Low-Value Paths

- Do not keep treating `preferences` as a general-purpose key-value dump.
- Do not add more raw Notion JSON tools without schema-bound validators.
- Do not inject full mechanical tool history into model context.
- Do not let retries update breaker counters before final outcome classification.
- Do not make production/deployed smoke the default validation path.
- Do not rely on mocked unit tests as the only signal for external service usability.

## Suggested Acceptance Chain

Default local gate:

```bash
uv run pytest tests/skills/test_exec_skill.py tests/skills/test_tmux_skill.py tests/skills/test_log_inspector_skill.py tests/core/test_progressive_disclosure.py -q
npm run test -- --run src/utils/__tests__/markdownRenderer.spec.ts src/views/__tests__/ChatView.spec.ts
```

Default full test-mode gate after implementation milestones:

```bash
bash test_run.sh
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

Production/deployment acceptance should be explicit and separate, using `--force` only when the operator confirms production validation.

## Rollback Points

- R1: disable new breaker weighting and fall back to old `status` counters.
- R3: restore from `preferences` backup and keep typed memory read-only.
- R5: route all events through old single consumer behind a feature flag.
- R6: disable virtualization/chunk buffering feature flags and render legacy message list.
- R7: disable Codex SDK lane and leave old skills active.

## Next Planning Input

Use this report as the input for the next `/hypo-workflow:plan`. The next plan should start with R1 and R2 unless the user chooses the aggressive Codex SDK migration path; even then, outcome taxonomy and trace schema should still come first.
