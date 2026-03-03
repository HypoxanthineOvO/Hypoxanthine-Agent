# M1 Gateway + WebUI Minimal Loop Design

## Scope

Build the first end-to-end closed loop for Hypo-Agent M1:
- FastAPI WebSocket gateway (`/ws`) with token auth and echo mode
- Vue 3 chat view that sends messages and receives echo replies
- Frontend/backend integration with connection status and nginx reverse proxy

## Current Context Notes

- `docs/architecture.md` is currently empty (0 lines), so this design is based on repository structure and M1 requirements.
- Existing message contract is `hypo_agent.models.Message` and must be used for WebSocket payload validation.
- `config/security.yaml` currently has whitelist and circuit-breaker settings, but no auth token key yet.

## Important Questions and Decisions

1. Where should the WebSocket auth token live?
- Decision: add top-level `auth_token` to `config/security.yaml` (e.g., `auth_token: dev-token-change-me`).
- Reason: simplest path for M1 and directly satisfies "read token from security.yaml".

2. How should browser clients pass auth token for WebSocket?
- Decision: use query parameter `?token=...` for M1.
- Reason: browser WebSocket API cannot reliably set arbitrary auth headers; query token is the lowest-friction option for local loop validation.

3. How strict is the message schema?
- Decision: backend accepts JSON payload and validates with `Message.model_validate(...)`; invalid payload returns structured WS error and closes connection.
- Reason: enforces contract early and avoids silent schema drift.

4. How should echo reply be shaped?
- Decision: return a `Message` with same `session_id`, reply `sender="assistant"`, and echoed `text`.
- Reason: keeps UI logic simple while establishing clear user/assistant message roles.

## Approach Options

### Option A: Single process serves both API and frontend static
- Pros: fewer moving parts in M1.
- Cons: harder to match production layout with nginx reverse proxy requirement.

### Option B (Recommended): Split services with nginx in front
- Backend: FastAPI/Uvicorn for `/ws`
- Frontend: Vite build output served by nginx
- nginx proxies `/ws` upgrade traffic to backend
- Pros: aligns with M1.3 requirement and future deployment shape.
- Cons: slightly more config upfront.

### Option C: Skip nginx in M1 and document later
- Pros: fastest to implement.
- Cons: directly misses an explicit M1 deliverable.

Recommendation: **Option B**.

## Proposed M1 Design

## Backend (M1.1)

- `src/hypo_agent/gateway/settings.py`
  - load and validate `config/security.yaml`
  - extract `auth_token` and existing security fields
- `src/hypo_agent/gateway/middleware.py`
  - ASGI middleware for WebSocket token check on `/ws`
  - reject unauthorized with close code `4401`
- `src/hypo_agent/gateway/ws.py`
  - `/ws` endpoint
  - validate inbound payload with `Message`
  - echo back validated `Message` response
- `src/hypo_agent/gateway/app.py`
  - `create_app()` factory; wire middleware + routes
- `src/hypo_agent/gateway/main.py`
  - uvicorn startup entrypoint for `python -m hypo_agent.gateway.main`

## Frontend (M1.2)

- `web/` Vite + Vue 3 + TypeScript project
- `ChatView` with:
  - message list
  - text input + send
  - connection status indicator (connecting/connected/disconnected/error)
  - markdown rendering (`markdown-it`, `html: false`)
- WebSocket composable/service for connection, send, receive, reconnect basics
- PWA via `vite-plugin-pwa` with minimal manifest and service worker registration

## Integration + Proxy (M1.3)

- WebSocket URL convention:
  - dev: `ws://127.0.0.1:8000/ws?token=...`
  - proxied: `ws(s)://<host>/ws?token=...`
- nginx config with:
  - static SPA hosting (`try_files ... /index.html`)
  - `/ws` upgrade proxy headers

## TDD Strategy

- Backend: pytest first (RED), then implementation (GREEN), then cleanup (REFACTOR)
- Frontend: vitest/vue-test-utils tests first for chat behavior + status + markdown
- Integration:
  - backend WS tests with FastAPI TestClient
  - frontend unit tests for composable/view
  - smoke test checklist for full stack and nginx proxy

## Assumptions to Confirm During Review

- `auth_token` key name and placement in `security.yaml` are acceptable.
- Echo response shape (`sender="assistant"` + same text/session) matches expected UX.
- nginx config path can be introduced as `deploy/nginx/hypo-agent.conf`.
