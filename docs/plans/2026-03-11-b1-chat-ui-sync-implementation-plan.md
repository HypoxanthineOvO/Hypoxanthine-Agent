# B1+ Chat UI Sync Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver single-session Chat UI simplification, cross-channel message sync, mobile navigation, and welcome-state guidance without changing core AI business logic.

**Architecture:** Keep the current FastAPI + WebSocket + in-memory dispatcher structure, but add sender-aware WebUI broadcast semantics and explicit QQ mirror notifications for WebUI-origin conversations. On the frontend, collapse ChatView into a single-session workspace and teach the websocket composable to merge full synced messages with the existing streaming flow.

**Tech Stack:** FastAPI, Pydantic, Vue 3, Naive UI, Vitest, pytest.

---

### Task 1: Lock Down Backend Sync Expectations

**Files:**
- Modify: `tests/gateway/test_ws_echo.py`
- Modify: `tests/gateway/test_ws_push.py`
- Create/Modify: `tests/gateway/test_webui_qq_sync.py`

**Step 1: Write the failing tests**

- Add websocket tests for:
  - WebUI sender message syncing to a second WebUI client
  - QQ-origin user message syncing to WebUI
  - WebUI-origin assistant reply syncing to other WebUI clients
  - WebUI-origin QQ mirror notification formatting and truncation

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/gateway/test_ws_echo.py tests/gateway/test_ws_push.py tests/gateway/test_webui_qq_sync.py`

Expected: new sync/mirror tests fail because sender-aware broadcast and QQ mirroring are not implemented.

**Step 3: Write minimal implementation**

- Extend websocket transport and app wiring just enough to satisfy the failing sync tests.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/gateway/test_ws_echo.py tests/gateway/test_ws_push.py tests/gateway/test_webui_qq_sync.py`

Expected: PASS.

### Task 2: Implement Backend Sender-Aware Sync

**Files:**
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/channels/qq_channel.py`
- Modify: `src/hypo_agent/core/pipeline.py`

**Step 1: Write the failing test**

- Add or refine one backend test that proves the originating WebUI socket keeps streaming while another WebUI socket receives the finalized synced message.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/gateway/test_webui_qq_sync.py -k other_webui_client`

Expected: FAIL.

**Step 3: Write minimal implementation**

- Add per-connection identity and exclude-current-client broadcast.
- Broadcast inbound WebUI user messages to peer WebUI clients.
- Broadcast QQ inbound user messages to WebUI.
- Prevent default plain QQ assistant duplication for WebUI-origin flows; use explicit mirror notifications instead.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/gateway/test_webui_qq_sync.py`

Expected: PASS.

### Task 3: Lock Down Frontend Single-Session UX

**Files:**
- Modify: `web/src/views/__tests__/ChatView.spec.ts`
- Modify: `web/src/composables/__tests__/useChatSocket.spec.ts`
- Create: `web/src/__tests__/App.spec.ts`

**Step 1: Write the failing tests**

- Add tests for:
  - hidden session sidebar / hidden new-session control
  - empty-state welcome screen
  - quick prompt fills composer
  - synced message with `channel="qq"` renders source badge
  - mobile nav trigger visible in `App.vue`

**Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- ChatView useChatSocket App`

Expected: FAIL on the new expectations.

**Step 3: Write minimal implementation**

- Refactor ChatView and App layout to satisfy the UX tests.

**Step 4: Run test to verify it passes**

Run: `cd web && npm run test -- ChatView useChatSocket App`

Expected: PASS.

### Task 4: Implement Frontend Sync + Mobile UI

**Files:**
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/composables/useChatSocket.ts`
- Modify: `web/src/components/chat/MessageBubble.vue`
- Modify: `web/src/types/message.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/components/layout/SideNav.vue`

**Step 1: Write the failing test**

- Add one focused composable test for deduplicating/merging streamed replies with externally synced full messages.

**Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- useChatSocket`

Expected: FAIL.

**Step 3: Write minimal implementation**

- Handle full synced messages alongside stream events.
- Surface source badges.
- Replace the session-sidebar layout with a full-width chat surface and welcome hero.
- Add mobile drawer navigation.

**Step 4: Run test to verify it passes**

Run: `cd web && npm run test -- ChatView useChatSocket App`

Expected: PASS.

### Task 5: Full Verification

**Files:**
- Verify only

**Step 1: Run backend tests**

Run: `pytest -q`

Expected: PASS.

**Step 2: Run frontend tests**

Run: `cd web && npm run test`

Expected: PASS.

**Step 3: Run manual browser verification**

Run:

```bash
python /home/heyx/Hypo-Agent/.codex/skills/webapp-testing/scripts/with_server.py \
  --server "PYTHONPATH=/home/heyx/Hypo-Agent/src python -c 'from hypo_agent.gateway.main import run; run(host=\"127.0.0.1\", port=18765)'" --port 18765 \
  --server "cd web && VITE_API_BASE=http://127.0.0.1:18765/api VITE_WS_URL=ws://127.0.0.1:18765/ws VITE_WS_TOKEN=dev-token-change-me npm run dev -- --host 127.0.0.1 --port 15173" --port 15173 \
  -- bash -lc 'echo ready'
```

Expected: both servers boot successfully for manual inspection.
