# M1: Gateway Skeleton + WebUI Minimal Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver M1 end-to-end chat loop: Vue chat sends a `Message` over WebSocket and receives backend echo reply, with token auth and nginx proxy path.

**Architecture:** Keep backend and frontend separately deployable. Backend exposes FastAPI `/ws` endpoint protected by WebSocket token middleware and validates payloads with `hypo_agent.models.Message`. Frontend is a Vite/Vue3/TS app with a chat view, markdown rendering, and PWA setup. nginx serves built frontend and proxies `/ws` upgrade traffic to Uvicorn.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, PyYAML, Pydantic v2, pytest, Vue 3, Vite, TypeScript, Vitest, markdown-it, vite-plugin-pwa, nginx

---

### Task 1: RED - Define Security Token Loading Contract

**Files:**
- Create: `tests/gateway/test_settings.py`

**Step 1: Write failing tests for token + security loading**

```python
from pathlib import Path

import pytest

from hypo_agent.gateway.settings import load_gateway_settings


def test_load_gateway_settings_reads_auth_token_and_security(tmp_path: Path):
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
auth_token: test-token
directory_whitelist:
  read: ["./docs"]
  write: ["./logs"]
  execute: ["./workflows"]
circuit_breaker:
  tool_level_max_failures: 3
  session_level_max_failures: 5
  cooldown_seconds: 120
  global_kill_switch: false
""".strip(),
        encoding="utf-8",
    )

    settings = load_gateway_settings(security_yaml)

    assert settings.auth_token == "test-token"
    assert settings.security.directory_whitelist.read == ["./docs"]


def test_load_gateway_settings_rejects_missing_token(tmp_path: Path):
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
directory_whitelist:
  read: ["./docs"]
  write: ["./logs"]
  execute: ["./workflows"]
circuit_breaker:
  tool_level_max_failures: 3
  session_level_max_failures: 5
  cooldown_seconds: 120
  global_kill_switch: false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="auth_token"):
        load_gateway_settings(security_yaml)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError` for `hypo_agent.gateway.settings`.

**Step 3: Commit RED state**

```bash
git add tests/gateway/test_settings.py
git commit -m "test(gateway): add failing tests for security token loader"
```

### Task 2: GREEN - Implement Gateway Settings Loader and Token Config

**Files:**
- Create: `src/hypo_agent/gateway/settings.py`
- Modify: `config/security.yaml`

**Step 1: Implement minimal loader**

```python
from pathlib import Path

import yaml
from pydantic import BaseModel

from hypo_agent.models import SecurityConfig


class GatewaySettings(BaseModel):
    auth_token: str
    security: SecurityConfig


def load_gateway_settings(path: Path | str = "config/security.yaml") -> GatewaySettings:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    token = (payload.get("auth_token") or "").strip()
    if not token:
        raise ValueError("auth_token is required in security.yaml")

    security_payload = {
        "directory_whitelist": payload.get("directory_whitelist", {}),
        "circuit_breaker": payload.get("circuit_breaker", {}),
    }
    security = SecurityConfig.model_validate(security_payload)
    return GatewaySettings(auth_token=token, security=security)
```

**Step 2: Add auth token in project config**

`config/security.yaml` add:

```yaml
auth_token: dev-token-change-me
```

**Step 3: Run tests to verify GREEN**

Run: `pytest tests/gateway/test_settings.py -v`
Expected: PASS.

**Step 4: Commit GREEN implementation**

```bash
git add src/hypo_agent/gateway/settings.py config/security.yaml
git commit -m "feat(gateway): load auth token from security config"
```

### Task 3: RED - Define WebSocket Auth + Echo Behavior

**Files:**
- Create: `tests/gateway/test_ws_echo.py`

**Step 1: Write failing endpoint tests**

```python
from fastapi.testclient import TestClient

from hypo_agent.gateway.app import create_app


def _client(token: str = "test-token") -> TestClient:
    app = create_app(auth_token=token)
    return TestClient(app)


def test_ws_rejects_missing_token():
    with _client() as client:
        with client.websocket_connect("/ws"):
            assert False, "should not connect"


def test_ws_rejects_invalid_token():
    with _client() as client:
        with client.websocket_connect("/ws?token=wrong"):
            assert False, "should not connect"


def test_ws_echoes_valid_message_payload():
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            response = ws.receive_json()
            assert response["sender"] == "assistant"
            assert response["text"] == "hello"
            assert response["session_id"] == "s1"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_ws_echo.py -v`
Expected: FAIL because `hypo_agent.gateway.app` does not exist.

**Step 3: Commit RED state**

```bash
git add tests/gateway/test_ws_echo.py
git commit -m "test(gateway): add failing websocket auth and echo tests"
```

### Task 4: GREEN - Implement FastAPI App, Middleware, and `/ws` Endpoint

**Files:**
- Create: `src/hypo_agent/gateway/middleware.py`
- Create: `src/hypo_agent/gateway/ws.py`
- Create: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/__init__.py`

**Step 1: Implement token middleware for WebSocket scope**

```python
from urllib.parse import parse_qs


class WsTokenAuthMiddleware:
    def __init__(self, app, auth_token: str, ws_path: str = "/ws"):
        self.app = app
        self.auth_token = auth_token
        self.ws_path = ws_path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and scope["path"] == self.ws_path:
            params = parse_qs(scope.get("query_string", b"").decode("utf-8"))
            token = (params.get("token") or [""])[0]
            if token != self.auth_token:
                await send({"type": "websocket.close", "code": 4401})
                return
        await self.app(scope, receive, send)
```

**Step 2: Implement `/ws` route with `Message` validation and echo**

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from hypo_agent.models import Message

router = APIRouter()


@router.websocket("/ws")
async def websocket_echo(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            inbound = Message.model_validate(payload)
            outbound = Message(
                text=inbound.text,
                image=inbound.image,
                file=inbound.file,
                audio=inbound.audio,
                sender="assistant",
                session_id=inbound.session_id,
            )
            await ws.send_json(outbound.model_dump(mode="json"))
    except ValidationError:
        await ws.close(code=4400)
    except WebSocketDisconnect:
        return
```

**Step 3: Wire app factory and middleware**

```python
from fastapi import FastAPI

from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.ws import router as ws_router


def create_app(auth_token: str) -> FastAPI:
    app = FastAPI(title="Hypo-Agent Gateway")
    app.include_router(ws_router)
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)
    return app
```

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/gateway/test_ws_echo.py -v`
Expected: PASS.

**Step 5: Commit GREEN implementation**

```bash
git add src/hypo_agent/gateway/middleware.py src/hypo_agent/gateway/ws.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/__init__.py
git commit -m "feat(gateway): add websocket auth middleware and echo endpoint"
```

### Task 5: RED - Define Uvicorn Entry Point Behavior

**Files:**
- Create: `tests/gateway/test_main.py`

**Step 1: Write failing startup test**

```python
from unittest.mock import patch

from hypo_agent.gateway.main import run


@patch("hypo_agent.gateway.main.uvicorn.run")
def test_run_starts_uvicorn_with_factory(mock_run):
    run(host="127.0.0.1", port=8000)
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8000
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_main.py -v`
Expected: FAIL because `hypo_agent.gateway.main` does not exist.

**Step 3: Commit RED state**

```bash
git add tests/gateway/test_main.py
git commit -m "test(gateway): add failing uvicorn startup test"
```

### Task 6: GREEN - Implement Gateway Runtime Entry Point

**Files:**
- Create: `src/hypo_agent/gateway/main.py`

**Step 1: Implement runtime bootstrap**

```python
import uvicorn

from hypo_agent.gateway.app import create_app
from hypo_agent.gateway.settings import load_gateway_settings


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    settings = load_gateway_settings()
    app = create_app(auth_token=settings.auth_token)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
```

**Step 2: Run tests to verify GREEN**

Run: `pytest tests/gateway/test_main.py -v`
Expected: PASS.

**Step 3: Commit GREEN implementation**

```bash
git add src/hypo_agent/gateway/main.py
git commit -m "feat(gateway): add uvicorn startup entrypoint"
```

### Task 7: REFACTOR - Backend Hardening and Regression Suite

**Files:**
- Modify: `tests/gateway/test_ws_echo.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/gateway/middleware.py`

**Step 1: Add invalid payload test (Message schema enforcement)**

```python
def test_ws_rejects_invalid_message_shape():
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"sender": "user"})  # missing session_id and content
            with pytest.raises(Exception):
                ws.receive_json()
```

**Step 2: Implement minimal close/error handling**
- close with code `4400` on invalid schema
- keep successful path unchanged

**Step 3: Run backend suite**

Run:
- `pytest tests/gateway -v`
- `pytest tests/test_models_serialization.py -v`
Expected: PASS.

**Step 4: Commit refactor**

```bash
git add tests/gateway src/hypo_agent/gateway
git commit -m "refactor(gateway): harden websocket validation and close codes"
```

### Task 8: Initialize Vue 3 + Vite + TypeScript + Test Tooling

**Files:**
- Delete: `web/.gitkeep`
- Create/Modify: `web/package.json`
- Create/Modify: `web/vite.config.ts`
- Create/Modify: `web/tsconfig.json`
- Create/Modify: `web/index.html`
- Create/Modify: `web/src/main.ts`
- Create/Modify: `web/src/App.vue`
- Create/Modify: `web/src/style.css`
- Create: `web/vitest.config.ts`
- Create: `web/src/test/setup.ts`

**Step 1: Scaffold project**

Run: `npm create vite@latest web -- --template vue-ts`
Expected: Vue TS scaffold generated in `web/`.

**Step 2: Add test + markdown + pwa dependencies**

Run: `npm install -D vitest @vitest/ui @vue/test-utils jsdom vite-plugin-pwa` (in `web/`)
Run: `npm install markdown-it` (in `web/`)
Expected: dependencies installed.

**Step 3: Verify tooling baseline**

Run: `npm run build` (in `web/`)
Expected: PASS.

**Step 4: Commit scaffold**

```bash
git add web
git commit -m "build(web): scaffold vue vite ts app with test tooling"
```

### Task 9: RED - Define Chat View and Connection Status Behavior

**Files:**
- Create: `web/src/views/__tests__/ChatView.spec.ts`
- Create: `web/src/composables/__tests__/useChatSocket.spec.ts`

**Step 1: Write failing UI tests**

```ts
import { mount } from "@vue/test-utils";
import ChatView from "../ChatView.vue";

test("shows disconnected status by default", () => {
  const wrapper = mount(ChatView);
  expect(wrapper.text()).toContain("Disconnected");
});

test("renders markdown in assistant message", async () => {
  const wrapper = mount(ChatView);
  await wrapper.vm.$data.messages.push({
    text: "**bold**",
    sender: "assistant",
    session_id: "s1",
  });
  expect(wrapper.html()).toContain("<strong>bold</strong>");
});
```

**Step 2: Run tests to verify RED**

Run: `npm run test -- ChatView.spec.ts` (in `web/`)
Expected: FAIL because `ChatView.vue` behavior/composable are not implemented.

**Step 3: Commit RED state**

```bash
git add web/src/views/__tests__/ChatView.spec.ts web/src/composables/__tests__/useChatSocket.spec.ts
git commit -m "test(web): add failing chat view and websocket composable tests"
```

### Task 10: GREEN - Implement Chat View, Markdown Rendering, and WebSocket Composable

**Files:**
- Create: `web/src/views/ChatView.vue`
- Create: `web/src/components/ConnectionStatus.vue`
- Create: `web/src/composables/useChatSocket.ts`
- Create: `web/src/types/message.ts`
- Modify: `web/src/App.vue`

**Step 1: Implement Message type (aligned to backend model fields)**

```ts
export interface Message {
  text?: string | null;
  image?: string | null;
  file?: string | null;
  audio?: string | null;
  sender: string;
  timestamp?: string;
  session_id: string;
}
```

**Step 2: Implement composable for ws lifecycle**
- states: `connecting | connected | disconnected | error`
- methods: `connect()`, `disconnect()`, `sendMessage()`
- callbacks: on message append

**Step 3: Implement chat view**
- input + send button
- message list
- markdown render with `markdown-it({ html: false, linkify: true })`
- connection status component

**Step 4: Run tests to verify GREEN**

Run: `npm run test` (in `web/`)
Expected: PASS.

**Step 5: Commit GREEN implementation**

```bash
git add web/src/views/ChatView.vue web/src/components/ConnectionStatus.vue web/src/composables/useChatSocket.ts web/src/types/message.ts web/src/App.vue
git commit -m "feat(web): implement websocket chat view with markdown and status"
```

### Task 11: Add PWA Support and Validate Build Output

**Files:**
- Modify: `web/vite.config.ts`
- Modify: `web/src/main.ts`
- Create: `web/public/pwa-192x192.png`
- Create: `web/public/pwa-512x512.png`

**Step 1: Configure `vite-plugin-pwa`**

```ts
VitePWA({
  registerType: "autoUpdate",
  manifest: {
    name: "Hypo-Agent",
    short_name: "Hypo",
    start_url: "/",
    display: "standalone",
    background_color: "#0b1020",
    theme_color: "#0b1020",
    icons: [
      { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
      { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
    ],
  },
})
```

**Step 2: Register SW in app entry**

```ts
import { registerSW } from "virtual:pwa-register";
registerSW({ immediate: true });
```

**Step 3: Verify PWA artifacts**

Run: `npm run build` (in `web/`)
Expected: PASS and dist contains service worker + manifest assets.

**Step 4: Commit PWA setup**

```bash
git add web/vite.config.ts web/src/main.ts web/public/pwa-192x192.png web/public/pwa-512x512.png
git commit -m "feat(web): enable pwa manifest and service worker registration"
```

### Task 12: RED/GREEN - nginx Reverse Proxy for WebSocket Upgrade

**Files:**
- Create: `tests/integration/test_nginx_ws_proxy_config.py`
- Create: `deploy/nginx/hypo-agent.conf`

**Step 1: Write failing config guard test**

```python
from pathlib import Path


def test_nginx_ws_proxy_has_upgrade_headers():
    content = Path("deploy/nginx/hypo-agent.conf").read_text(encoding="utf-8")
    assert "proxy_set_header Upgrade $http_upgrade;" in content
    assert "proxy_set_header Connection \"upgrade\";" in content
    assert "location /ws" in content
```

**Step 2: Run test to verify RED**

Run: `pytest tests/integration/test_nginx_ws_proxy_config.py -v`
Expected: FAIL because config file does not exist yet.

**Step 3: Add nginx config**

```nginx
server {
    listen 80;
    server_name _;
    root /var/www/hypo-agent/web/dist;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 600s;
    }
}
```

**Step 4: Run test to verify GREEN**

Run: `pytest tests/integration/test_nginx_ws_proxy_config.py -v`
Expected: PASS.

**Step 5: Commit nginx proxy**

```bash
git add deploy/nginx/hypo-agent.conf tests/integration/test_nginx_ws_proxy_config.py
git commit -m "feat(deploy): add nginx websocket reverse proxy config"
```

### Task 13: Final Integration Verification and Docs

**Files:**
- Create: `docs/runbooks/m1-local-smoke-test.md`
- Modify: `.gitignore`

**Step 1: Add local smoke test runbook**
- backend start command
- frontend dev command
- sample ws URL and token
- expected UI behavior (connected, send, echo receive)
- optional nginx validation command

**Step 2: Update ignore rules for frontend artifacts**

Add to `.gitignore`:

```gitignore
web/node_modules/
web/dist/
web/.vite/
```

**Step 3: Run full verification**

Run:
- `pytest -q`
- `npm run test` (in `web/`)
- `npm run build` (in `web/`)

Expected: all PASS.

**Step 4: Final commit**

```bash
git add docs/runbooks/m1-local-smoke-test.md .gitignore
git commit -m "chore(m1): add smoke runbook and finalize gateway-webui closed loop"
```
