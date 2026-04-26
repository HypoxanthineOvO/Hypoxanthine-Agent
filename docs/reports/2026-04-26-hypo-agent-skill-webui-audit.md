# Hypo-Agent M3 Skill Acceptance And WebUI Audit

Date: 2026-04-26
Scope: skill acceptance credibility, smoke guardrails, WebUI chat/memory usability and performance.

## Executive Summary

M3 found no immediate regression in the narrow tests that were run, but the current acceptance surface is too shallow to support the claim that many skills are actually usable in production-like workflows. The WebUI also has clear hot paths that explain reported lag: streaming assistant text mutates the same reactive message on every chunk, the view recomputes the whole timeline, `TextMessage` rerenders markdown and post-processes math/mermaid on every text change, and the message list is not virtualized.

## Verification Performed

- `uv run pytest tests/skills/test_exec_skill.py tests/skills/test_tmux_skill.py tests/skills/test_log_inspector_skill.py tests/core/test_progressive_disclosure.py -q`
  - Result: 36 passed.
- `npm run test -- --run src/utils/__tests__/markdownRenderer.spec.ts src/views/__tests__/ChatView.spec.ts`
  - Result: 23 passed across 2 files.

These are useful regression tests, not sufficient acceptance gates for external skills or large-session WebUI performance.

## Findings

### High: Skill tests are mostly unit/mocked and do not prove real usability

Evidence:

- `tests/skills/test_notion_skill.py` relies on `FakeNotionClient`, so it verifies formatting and payload wiring, but not Notion schema discovery, property-name mismatch recovery, API validation messages, or retry behavior against real failures.
- `tests/skills/test_memory_skill.py` only covers `save_preference`, `get_preference`, and upsert behavior against a temp SQLite DB. It does not cover memory classification, lifecycle state, consolidation, language policy, or conflict resolution.
- `tests/skills/auth/test_login_providers.py` uses fake Playwright pages and locators for most provider logic. Integration tests exist, but are marked `@pytest.mark.integration` and are not part of the narrow default gate.
- Runtime evidence from M1 shows high error counts in `run_command`, `notion_query_db`, `read_file`, and unknown tool names even though local test suites pass.

Impact:

Passing unit tests can coexist with skills that fail in real conversation because the tests do not exercise model-facing instructions, degraded external APIs, user-language prompts, schema introspection, or multi-step recovery.

Recommendation:

Add per-skill acceptance packs with three layers: pure unit tests, mocked contract tests, and opt-in sandbox/e2e tests. Each skill should publish a machine-readable contract with required config, side-effect class, timeout budget, retry policy, and sample model-facing invocations.

### High: WebUI streaming path does too much synchronous work per chunk

Evidence:

- `web/src/composables/useChatSocket.ts:600-637` appends each assistant chunk directly to the existing reactive message text.
- `web/src/views/ChatView.vue:91-118` computes `displayedMessages` and `timelineItems` from the full message array.
- `web/src/views/ChatView.vue:170` schedules scroll based on message length and last text length, so every chunk schedules UI work.
- `web/src/components/chat/TextMessage.vue:48-57` watches `props.text` and calls `renderMarkdown(text)` in template rendering, then runs async `renderMathIn` and `renderMermaidIn`.
- `web/src/utils/markdownRenderer.ts:133-159` renders markdown with highlight support; `189-241` scans and mutates DOM for KaTeX and Mermaid.

Impact:

Long assistant streams, large code blocks, or many historical messages can produce repeated markdown parsing, syntax highlighting, DOM scans, layout changes, and forced scrolling. This matches the reported “WebUI 卡顿难用”.

Recommendation:

Buffer assistant chunks and commit at a fixed frame/rate, memoize rendered markdown by message id/text version, defer KaTeX/Mermaid until stream completion unless the block is closed, and virtualize chat rows for long sessions.

### Medium: Tool event history is still rendered as many durable rows

Evidence:

- `web/src/composables/useChatSocket.ts:663-712` pushes every tool start/result into `messages.value`.
- `web/src/composables/useChatSocket.ts:255-291` also maintains a collapsed pipeline/progress message, which means tool events can appear both as progress state and as full message rows.
- `web/src/components/chat/ChatMessageList.vue:72-94` renders all timeline items without virtualization.

Impact:

History files and WebUI can become dominated by mechanical tool rows. This hurts small models by polluting conversational history and hurts WebUI performance by increasing rendered nodes.

Recommendation:

Persist tool traces in a structured trace store by default, keep only summarized user-facing milestones in chat history, and expose raw traces behind an expandable debug panel.

### Medium: Memory UI exposes tables, not memory semantics

Evidence:

- `web/src/views/MemoryView.vue:84-92` defaults to SQLite table browsing and raw `preferences` editing.
- `web/src/views/MemoryView.vue:196-218` saves only `pref_value` for `preferences`.
- `web/src/views/MemoryView.vue:316-350` shows generic table columns and a raw preference editor.

Impact:

The UI reinforces the current polluted `preferences` model instead of helping operators understand memory categories, stale operational state, confidence, source, language, or consolidation needs.

Recommendation:

Replace the primary memory screen with typed views: user profile, interaction rules, operational state, credentials/auth state, knowledge notes, and cleanup queue. Keep raw SQLite browser as an admin/debug tab.

### Medium: Smoke guardrails are good, but acceptance remains deployment-dependent

Evidence:

- `test_run.sh` sets `HYPO_TEST_MODE=1`.
- `scripts/agent_cli.py:672-712` refuses smoke outside test mode unless `--force`, refuses production port `8765` in test mode, and refuses smoke if QQ is connected.
- `scripts/agent_cli.py:721-751` still depends on live assistant responses for several cases.

Impact:

The safety boundary is solid, but smoke results may vary with model/provider state and do not isolate skill usability failures from model planning failures.

Recommendation:

Split smoke into deterministic API/skill probes and conversational smoke. Conversational smoke should be a separate gate with recorded traces and failure taxonomy.

## Acceptance Gate Matrix

| Area | Current Confidence | Required Gate |
| --- | --- | --- |
| Exec/tmux/log inspector | Medium | Existing unit tests plus policy-denial taxonomy and shell profile fixtures |
| Notion | Low | Sandbox database with schema discovery, missing-property repair, validation error parsing, and timeout/retry tests |
| Memory | Low | Typed memory CRUD, classifier evals, consolidation jobs, Chinese-first preference policy, conflict handling |
| Browser/auth | Low-Medium | Mocked provider contracts plus opt-in Playwright sandbox login flows |
| WebUI chat | Medium for unit behavior, low for performance | Long-session virtualization test, streamed 10k-token markdown test, tool-trace burst test |
| WebUI memory | Low | Typed semantic memory workflows and destructive action confirmations |
| Default smoke | Medium safety, low diagnosis | Deterministic probes separated from model conversational smoke |

## Refactor Implications For M4

- Treat skill reliability as a contract/testing problem, not only prompt wording.
- Reduce tool trace exposure to models and UI by moving raw calls into structured traces.
- Rework streaming rendering before adding more WebUI features.
- Memory refactor needs both backend taxonomy and UI semantics; only changing prompt instructions will not solve the problem.
