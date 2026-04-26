# WebUI Performance, Memory, And Codex Job UI

Date: 2026-04-26

## Purpose

R7 makes the WebUI usable for long running sessions and typed memory operations without changing the backend message contract for older clients.

The main changes are:

- assistant streaming chunks are buffered before committing to Vue state;
- markdown rendering is cached by message key/version;
- KaTeX and Mermaid post-rendering is deferred while a streamed block is incomplete;
- long chat histories render only the latest window until the user requests older messages;
- Codex status appears as concise status cards and a job panel instead of raw transcript rows;
- Memory starts with typed semantic memory and keeps raw SQLite browsing as a debug tab.

## Streaming And Markdown

`useChatSocket` now batches `assistant_chunk` events on a short cadence before mutating the active assistant message. The message keeps `metadata.streaming=true` until `assistant_done`, then switches to a final render version. This reduces Vue updates during token bursts.

`markdownRenderer.ts` exposes:

- `renderMarkdown(source, { cacheKey, version, streaming })`
- `clearMarkdownRenderCache()`
- `getMarkdownRenderCacheStats()`
- `shouldRenderEnhancedMarkdown(source, { streaming })`

`TextMessage` uses those options through `MarkdownPreview`. While streaming, unclosed code fences or display math blocks skip KaTeX/Mermaid enhancement. Once the stream is complete, the final version is rendered normally.

## Long History

`ChatView` windows visible chat messages with a 160-message page size. Older rows remain in the client state but are not rendered until the user clicks the older-message control. This is pagination rather than full virtualization, chosen because it is low-risk and fits the current message-list structure.

## Codex Jobs

Runtime Codex tool-status messages are routed through `CodexStatusCard`. The chat timeline shows task id, status, and summary only. Raw transcript text is not expanded inline.

`ChatView` also loads `/api/sessions/{session_id}/coder-tasks` and renders `CodexJobPanel` with recent task status, working directory, attached state, and completion state.

## Typed Memory UI

The Memory page now opens on the typed memory view. It lists memory items by:

- `memory_class`
- `key`
- `value`
- `source`
- `confidence`
- `updated_at`
- injection eligibility

Prompt-injectable classes are:

- `interaction_policy`
- `knowledge_note`
- `sop`
- `user_profile`

`operational_state` and `credentials_state` remain visible for audit/editing, but they are shown as non-injectable.

The raw SQLite table browser remains available as `SQLite Debug`.

## API

R7 adds typed memory endpoints:

- `GET /api/memory/items?status=active&memory_class=...`
- `POST /api/memory/items`

Both require the normal API token. `GET` returns `injection_eligible` per item and the injectable class list used by the WebUI. `POST` upserts through `StructuredStore.save_memory_item`.
