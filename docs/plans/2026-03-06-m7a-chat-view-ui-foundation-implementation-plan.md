# M7a Chat View + UI Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver M7a end-to-end: upgraded chat rendering, compressed output retrieval, media/file support, frontend UI foundation, input UX upgrades, and backend RichResponse/channel-adapter scaffolding.

**Architecture:** Keep the existing WS event protocol backward-compatible while introducing `RichResponse` + `ChannelAdapter` as an internal abstraction boundary for future channels. Build frontend rendering as composable message components backed by a single markdown renderer utility. Add new REST endpoints (`/api/compressed/{cache_id}`, `/api/files`) with strict token + whitelist enforcement and unify WS/REST error handling.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, structlog, pytest, Vue 3 + Vite + TypeScript, Naive UI, markdown-it ecosystem, vitest.

---

## Phase Overview

1. Phase A: Backend architecture reserve + DB table (`M7a.7` core)
2. Phase B: Compressed original retrieval + WS protocol extension (`M7a.2` backend)
3. Phase C: File serving API with permission + token checks (`M7a.4` backend)
4. Phase D: Frontend UI foundation and transport resilience (`M7a.5`)
5. Phase E: Markdown engine + message component system (`M7a.1` + `M7a.3`)
6. Phase F: Compressed message UI, media rendering, input UX and hotkeys (`M7a.2` frontend + `M7a.4` frontend + `M7a.6`)
7. Phase G: Integration tests, nginx update, and docs handoff

---

### Task 1: Add RichResponse and ChannelAdapter skeleton

**Files:**
- Create: `src/hypo_agent/core/rich_response.py`
- Create: `src/hypo_agent/core/channel_adapter.py`
- Test: `tests/core/test_rich_response.py`
- Test: `tests/core/test_channel_adapter.py`

**Step 1: Write failing tests for RichResponse and WebUIAdapter passthrough**
Run: `pytest tests/core/test_rich_response.py tests/core/test_channel_adapter.py -q`
Expected: FAIL (new modules missing)

**Step 2: Add minimal dataclass/protocol implementation**
- `RichResponse` fields: `text`, `compressed_meta`, `tool_calls`, `attachments`
- `ChannelAdapter` protocol with `format(...)`
- `WebUIAdapter` passthrough implementation for current WS schema

**Step 3: Verify tests**
Run: `pytest tests/core/test_rich_response.py tests/core/test_channel_adapter.py -q`
Expected: PASS

**Step 4: Commit**
```bash
git add src/hypo_agent/core/rich_response.py src/hypo_agent/core/channel_adapter.py tests/core/test_rich_response.py tests/core/test_channel_adapter.py
git commit -m "feat(m7a): add rich response and channel adapter skeleton"
```

---

### Task 2: Add tool_invocations table and persistence hook

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/memory/test_structured_store.py`
- Test: `tests/skills/test_skill_manager.py`
- Test: `tests/gateway/test_app_deps_permissions.py`

**Step 1: Write failing tests for table creation + record insert**
Run: `pytest tests/memory/test_structured_store.py::test_structured_store_tool_invocations tests/skills/test_skill_manager.py::test_skill_manager_records_tool_invocations -q`
Expected: FAIL

**Step 2: Implement DB schema and indices in `init()`**
- Create table `tool_invocations`
- Create required 3 indices

**Step 3: Implement write method and hook into SkillManager.invoke()**
- Write on success/failure/timeout/blocked
- `result_preview` truncate 500 chars
- Include `duration_ms`, `error_info`

**Step 4: Verify tests**
Run: `pytest tests/memory/test_structured_store.py tests/skills/test_skill_manager.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/memory/structured_store.py src/hypo_agent/core/skill_manager.py src/hypo_agent/gateway/app.py tests/memory/test_structured_store.py tests/skills/test_skill_manager.py tests/gateway/test_app_deps_permissions.py
git commit -m "feat(m7a): add tool invocation persistence"
```

---

### Task 3: Add `/api/compressed/{cache_id}` and compressed metadata event field

**Files:**
- Create: `src/hypo_agent/gateway/compressed_api.py`
- Modify: `src/hypo_agent/core/output_compressor.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/gateway/test_compressed_api.py`
- Test: `tests/core/test_output_compressor.py`
- Test: `tests/gateway/test_ws_echo.py`

**Step 1: Write failing API test for original retrieval**
Run: `pytest tests/gateway/test_compressed_api.py -q`
Expected: FAIL

**Step 2: Extend compressor return contract with `cache_id` access**
- Preserve existing `compress_if_needed(...)` behavior
- Add helper to expose metadata safely without breaking old tests

**Step 3: Add REST endpoint**
- `GET /api/compressed/{cache_id}`
- Return 404 when missing cache id
- Add structlog events for hit/miss

**Step 4: Add `compressed_meta` to `tool_call_result`**
- Defer pipeline emission to Task 3.5
- Keep endpoint and compressor metadata plumbing ready

**Step 5: Verify tests**
Run: `pytest tests/gateway/test_compressed_api.py tests/core/test_output_compressor.py tests/gateway/test_ws_echo.py -q`
Expected: PASS

**Step 6: Commit**
```bash
git add src/hypo_agent/gateway/compressed_api.py src/hypo_agent/core/output_compressor.py src/hypo_agent/gateway/app.py tests/gateway/test_compressed_api.py tests/core/test_output_compressor.py tests/gateway/test_ws_echo.py
git commit -m "feat(m7a): support compressed output retrieval and meta"
```

---

### Task 3.5: Integrate RichResponse at pipeline exit via WebUIAdapter

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Test: `tests/core/test_pipeline_tools.py`
- Test: `tests/gateway/test_ws_echo.py`

**Step 1: Write failing compatibility tests around adapter-formatted events**
Run: `pytest tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py -q`
Expected: FAIL

**Step 2: Build RichResponse objects in pipeline and format through `WebUIAdapter.format()`**
- Convert pipeline internal output to `RichResponse`
- Keep current WS events unchanged: `assistant_chunk`, `assistant_done`, `tool_call_start`, `tool_call_result`
- Include `compressed_meta` in `tool_call_result` only when compressed

**Step 3: Verify backward compatibility**
Run: `pytest tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py -q`
Expected: PASS

**Step 4: Commit**
```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/ws.py tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py
git commit -m "feat(m7a): integrate rich response adapter in pipeline"
```

---

### Task 4: Add secure `/api/files?path=...` endpoint

**Files:**
- Create: `src/hypo_agent/gateway/files_api.py`
- Create: `src/hypo_agent/gateway/auth.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/middleware.py`
- Test: `tests/gateway/test_files_api.py`
- Test: `tests/gateway/test_sessions_api_cors.py`

**Step 1: Write failing tests for token/permission checks**
Run: `pytest tests/gateway/test_files_api.py -q`
Expected: FAIL

**Step 2: Implement HTTP token check helper**
- Reuse gateway auth token from app settings
- Accept `Authorization: Bearer <token>` and query fallback `token`

**Step 3: Implement file API with whitelist guard**
- Validate path presence
- `PermissionManager.check_permission(path, "read")`
- Stream file contents with proper media type
- Log allow/deny/not-found events

**Step 4: Verify tests**
Run: `pytest tests/gateway/test_files_api.py tests/gateway/test_sessions_api_cors.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/files_api.py src/hypo_agent/gateway/auth.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/middleware.py tests/gateway/test_files_api.py tests/gateway/test_sessions_api_cors.py
git commit -m "feat(m7a): add secure file serving api"
```

---

### Task 5: Unify WS error shape and retryability metadata

**Files:**
- Modify: `src/hypo_agent/gateway/ws.py`
- Test: `tests/gateway/test_ws_echo.py`

**Step 1: Write failing WS error-contract tests**
Run: `pytest tests/gateway/test_ws_echo.py::test_ws_sends_structured_error_event -q`
Expected: FAIL

**Step 2: Implement error envelope**
- Shape: `{"type":"error","code":"...","message":"...","retryable":bool,"session_id":"..."}`
- Map runtime/model timeout/network-ish errors to `retryable=true`

**Step 3: Verify tests**
Run: `pytest tests/gateway/test_ws_echo.py -q`
Expected: PASS

**Step 4: Commit**
```bash
git add src/hypo_agent/gateway/ws.py tests/gateway/test_ws_echo.py
git commit -m "feat(m7a): standardize websocket error events"
```

---

### Task 6: Frontend foundation with Naive UI + layout + theme

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/src/main.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/style.css`
- Create: `web/src/components/layout/SideNav.vue`
- Create: `web/src/composables/useThemeMode.ts`
- Create: `web/src/utils/apiClient.ts`
- Test: `web/src/utils/__tests__/apiClient.spec.ts`

**Step 1: Write failing tests for API interceptor behavior**
Run: `cd web && npm test -- src/utils/__tests__/apiClient.spec.ts`
Expected: FAIL

**Step 2: Install and wire Naive UI**
Run: `cd web && npm install naive-ui`

**Step 3: Implement 3-breakpoint app shell + side nav placeholders**
- Desktop fixed sidebar, tablet collapsible, mobile single panel
- Icons: `💬📊⚙️🧠`, only Chat enabled
- Tooltip for disabled tabs: `Coming in M7b`

**Step 4: Implement OS theme detect + manual toggle (`Ctrl/Cmd+D`)**
- Add global theme state in composable

**Step 5: Verify tests/build**
Run: `cd web && npm run test && npm run build`
Expected: PASS

**Step 6: Commit**
```bash
git add web/package.json web/package-lock.json web/src/main.ts web/src/App.vue web/src/style.css web/src/components/layout/SideNav.vue web/src/composables/useThemeMode.ts web/src/utils/apiClient.ts web/src/utils/__tests__/apiClient.spec.ts
git commit -m "feat(m7a): add naive-ui app shell and theme foundation"
```

---

### Task 7: WS auto-reconnect and frontend error notifications

**Files:**
- Modify: `web/src/composables/useChatSocket.ts`
- Modify: `web/src/components/ConnectionStatus.vue`
- Modify: `web/src/views/ChatView.vue`
- Create: `web/src/components/layout/ReconnectBanner.vue`
- Test: `web/src/composables/__tests__/useChatSocket.spec.ts`

**Step 1: Write failing reconnect tests**
Run: `cd web && npm test -- src/composables/__tests__/useChatSocket.spec.ts`
Expected: FAIL

**Step 2: Implement exponential backoff reconnect**
- Delays: `1s, 2s, 4s, 8s, 16s, 30s cap`
- Ensure no duplicate timers and proper cleanup

**Step 3: Add WS error handling UI integration**
- Consume backend `error` events
- Use Naive UI notification; retryable adds retry action
- Top banner on network disconnected

**Step 4: Verify tests**
Run: `cd web && npm test -- src/composables/__tests__/useChatSocket.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/composables/useChatSocket.ts web/src/components/ConnectionStatus.vue web/src/views/ChatView.vue web/src/components/layout/ReconnectBanner.vue web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "feat(m7a): add websocket reconnect and unified error ux"
```

---

### Task 8: Build markdown renderer utility and code block enhancement

**Files:**
- Create: `web/src/utils/markdownRenderer.ts`
- Create: `web/src/components/chat/CodeBlock.vue`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Test: `web/src/utils/__tests__/markdownRenderer.spec.ts`
- Test: `web/src/components/chat/__tests__/CodeBlock.spec.ts`

**Step 1: Write failing renderer tests**
Run: `cd web && npm test -- src/utils/__tests__/markdownRenderer.spec.ts`
Expected: FAIL

**Step 2: Install markdown dependencies**
Run: `cd web && npm install markdown-it-task-lists @traptitech/markdown-it-katex katex mermaid highlight.js`
- If compatibility issues appear, fallback: `cd web && npm install markdown-it-texmath katex`

**Step 3: Implement renderer utility**
- Central plugin registration
- GFM tables + task lists
- KaTeX support via `@traptitech/markdown-it-katex` (fallback `markdown-it-texmath`)
- Explicitly import KaTeX CSS
- Mermaid lazy render hook
- Ensure nested lists, quote, inline code behaviors

**Step 4: Implement CodeBlock component**
- Language label
- Line numbers
- Copy button with user feedback

**Step 5: Verify tests/build**
Run: `cd web && npm run test && npm run build`
Expected: PASS

**Step 6: Commit**
```bash
git add web/src/utils/markdownRenderer.ts web/src/components/chat/CodeBlock.vue web/package.json web/package-lock.json web/src/utils/__tests__/markdownRenderer.spec.ts web/src/components/chat/__tests__/CodeBlock.spec.ts
git commit -m "feat(m7a): add unified markdown renderer and enhanced code blocks"
```

---

### Task 9: Split message rendering into component tree

**Files:**
- Create: `web/src/components/chat/MessageBubble.vue`
- Create: `web/src/components/chat/TextMessage.vue`
- Create: `web/src/components/chat/MarkdownPreview.vue`
- Create: `web/src/components/chat/MediaMessage.vue`
- Create: `web/src/components/chat/CompressedMessage.vue`
- Create: `web/src/components/chat/ToolCallMessage.vue`
- Create: `web/src/components/chat/FileAttachment.vue`
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/types/message.ts`
- Test: `web/src/components/chat/__tests__/ToolCallMessage.spec.ts`
- Test: `web/src/components/chat/__tests__/CompressedMessage.spec.ts`
- Test: `web/src/components/chat/__tests__/MediaMessage.spec.ts`
- Test: `web/src/views/__tests__/ChatView.spec.ts`

**Step 1: Write failing component tests for render variants**
Run: `cd web && npm test -- src/components/chat/__tests__/ToolCallMessage.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: FAIL

**Step 2: Implement tool call collapsed summary + expand details**
- Summary format: `🔧 执行了 run_command("...") → 成功/失败`

**Step 3: Implement markdown preview toggle and compressed message placeholder**
- `<> 源码` toggle for markdown files
- `📄 查看原文` lazy trigger slot

**Step 4: Wire ChatView to new component tree**
- Keep left/right bubble alignment
- Improve spacing and max width

**Step 5: Add future type reservation fields**
- `senderName?: string`
- `senderAvatar?: string`
- `channel?: string`

**Step 6: Verify tests/build**
Run: `cd web && npm run test && npm run build`
Expected: PASS

**Step 7: Commit**
```bash
git add web/src/components/chat/MessageBubble.vue web/src/components/chat/TextMessage.vue web/src/components/chat/MarkdownPreview.vue web/src/components/chat/MediaMessage.vue web/src/components/chat/CompressedMessage.vue web/src/components/chat/ToolCallMessage.vue web/src/components/chat/FileAttachment.vue web/src/views/ChatView.vue web/src/types/message.ts web/src/components/chat/__tests__/ToolCallMessage.spec.ts web/src/components/chat/__tests__/CompressedMessage.spec.ts web/src/components/chat/__tests__/MediaMessage.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "feat(m7a): split chat rendering into component system"
```

---

### Task 10: Implement compressed original lazy fetch and media/file heuristics

**Files:**
- Modify: `web/src/components/chat/CompressedMessage.vue`
- Modify: `web/src/components/chat/MediaMessage.vue`
- Modify: `web/src/components/chat/FileAttachment.vue`
- Modify: `web/src/utils/apiClient.ts`
- Modify: `web/src/views/ChatView.vue`
- Test: `web/src/components/chat/__tests__/CompressedMessage.spec.ts`
- Test: `web/src/components/chat/__tests__/MediaMessage.spec.ts`
- Test: `web/src/views/__tests__/ChatView.spec.ts`

**Step 1: Write failing lazy-fetch tests**
Run: `cd web && npm test -- src/components/chat/__tests__/CompressedMessage.spec.ts`
Expected: FAIL

**Step 2: Implement original content fetch flow**
- Call `GET /api/compressed/{cache_id}` only on click
- Cache result per message instance
- Show loading/error states (no silent failure)

**Step 3: Implement original format heuristic**
- `run_command` / `run_code` -> code block
- `.py/.yaml/.json` -> syntax mode
- `.md` -> markdown renderer
- fallback -> plain code block

**Step 4: Implement media/file render strategy**
- Image inline preview for supported suffixes
- Video inline player for `.mp4/.webm`
- Code files with header + copy

**Step 5: Verify tests/build**
Run: `cd web && npm run test && npm run build`
Expected: PASS

**Step 6: Commit**
```bash
git add web/src/components/chat/CompressedMessage.vue web/src/components/chat/MediaMessage.vue web/src/components/chat/FileAttachment.vue web/src/utils/apiClient.ts web/src/views/ChatView.vue web/src/components/chat/__tests__/CompressedMessage.spec.ts web/src/components/chat/__tests__/MediaMessage.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "feat(m7a): add compressed original retrieval and media rendering"
```

---

### Task 11: Input box UX and global hotkeys

**Files:**
- Create: `web/src/composables/useHotkey.ts`
- Modify: `web/src/views/ChatView.vue`
- Test: `web/src/composables/__tests__/useHotkey.spec.ts`
- Test: `web/src/views/__tests__/ChatView.spec.ts`

**Step 1: Write failing keyboard behavior tests**
Run: `cd web && npm test -- src/composables/__tests__/useHotkey.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: FAIL

**Step 2: Implement composable for registration lifecycle**
- register/unregister on mount/unmount
- normalize Ctrl/Cmd combos

**Step 3: Implement ChatView input upgrades**
- textarea autoresize max 200px
- `Ctrl/Cmd+Enter`: send message (Enter remains newline)
- `Esc`: close expanded input / collapse sidebar
- `Ctrl/Cmd+L`: clear current conversation
- `Ctrl/Cmd+N`: create new conversation
- `Ctrl/Cmd+D`: toggle dark/light mode
- `Ctrl/Cmd+K`: reserve no-op registration

**Step 4: Verify tests/build**
Run: `cd web && npm run test && npm run build`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/composables/useHotkey.ts web/src/views/ChatView.vue web/src/composables/__tests__/useHotkey.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "feat(m7a): improve composer ux and hotkey system"
```

---

### Task 12: Deployment + docs + full verification

**Files:**
- Modify: `deploy/nginx/hypo-agent.conf`
- Modify: `tests/integration/test_nginx_ws_proxy_config.py`
- Modify: `docs/architecture.md`
- Create: `docs/runbooks/m7a-chat-view-ui-foundation.md`

**Step 1: Update nginx for `/api` reverse proxy**
- Add `location /api` block to proxy to backend
- Preserve existing `/ws` settings

**Step 2: Add integration test assertions for `/api` proxy section**
Run: `pytest tests/integration/test_nginx_ws_proxy_config.py -q`
Expected: PASS

**Step 3: Update architecture and runbook docs**
- Document new endpoints, WS error contract, component architecture
- Include manual smoke checklist

**Step 4: Full verification suite**
Run:
- `pytest -q`
- `cd web && npm run test`
- `cd web && npm run build`
Expected: all PASS

**Step 5: Doc commit (required format)**
```bash
git add docs/architecture.md docs/runbooks/m7a-chat-view-ui-foundation.md
git commit -m "M7a[doc]: document chat view upgrades and ui foundation"
```

---

## Estimated Effort

- Phase A-B-C (backend): 2.5-3.0 人日
- Phase D-E-F (frontend): 3.0-3.5 人日
- Phase G (integration/docs): 0.5-1.0 人日
- Total: 6.0-7.5 人日

## Dependencies

- Task 1 before Task 3.5 (adapter scaffold before protocol integration)
- Task 2 can run in parallel with Task 3/4 after app-deps access is stable
- Task 3 before Task 3.5 (compressed metadata plumbing before pipeline emission)
- Task 6 before Task 7/9/11 (UI foundation first)
- Task 8 before Task 9/10 (renderer + code block as base)
- Task 12 after all feature tasks

## Risks and Notes

- Backward compatibility risk in WS event flow; keep `assistant_chunk`/`assistant_done` unchanged.
- Security risk in file API; enforce token + whitelist + path resolve checks.
- Mermaid render cost risk on long chats; lazy load and per-message mount guard.
- Reconnect risk causing duplicate streams; enforce single-socket state machine.
- Strict TypeScript settings may surface many type errors during component split; stage by typed event model first.
- Never silent-fail: every backend/WS/REST failure logs via structlog and surfaces user-visible error.
