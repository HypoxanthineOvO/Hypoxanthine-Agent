# M1 Local Smoke Test

## Prerequisites

- Conda env: `HypoAgent`
- Node.js 20+ with npm

## 1) Backend

From repo root:

```bash
python -m hypo_agent.gateway.main
```

Expected:
- Uvicorn starts on `0.0.0.0:8000`
- WebSocket endpoint available at `ws://127.0.0.1:8000/ws`

## 2) Frontend (dev mode)

In a second terminal:

```bash
cd web
VITE_WS_URL=ws://127.0.0.1:8000/ws VITE_WS_TOKEN=dev-token-change-me npm run dev
```

Open the local Vite URL and verify:
- Status initially shows `Disconnected`
- Click `Connect` then status changes to `Connected`
- Send `hello` and receive assistant echo `hello`
- Markdown message from backend is rendered (e.g. `**bold**`)

## 3) Frontend tests and build

```bash
cd web
npm run test
npm run build
```

Expected:
- Vitest passes
- Build outputs `dist/sw.js` and `dist/manifest.webmanifest`

## 4) nginx proxy (optional local validation)

- Config file: `deploy/nginx/hypo-agent.conf`
- `/ws` location must include upgrade headers:
  - `proxy_set_header Upgrade $http_upgrade;`
  - `proxy_set_header Connection "upgrade";`

If deploying behind nginx, verify browser WS URL uses proxied path:
- `ws://<host>/ws?token=<token>` (or `wss://` on HTTPS)
