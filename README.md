# Hypo-Agent

Single-user personal AI assistant with layered memory, skills, and a WebUI gateway.

## Features (M0-M9)

- Streaming chat gateway (FastAPI + WebSocket)
- Layered memory: L1 session (`.jsonl`); L2 structured store (SQLite); L3 knowledge base (Markdown, editable via WebUI)
- Skill system with permission controls (filesystem / tmux / code-run / reminders / email scan, etc.)
- Security hardening: token auth, directory whitelist, circuit breaker + kill switch
- Slash commands for ops and inspection (`/model status`, `/token*`, `/kill`, etc.)
- Output compression for long responses (with marker)
- WebUI (PWA) + Dashboard + Config editor + Memory editor
- Scheduler: reminders + heartbeat + email scan triggers
- Multi-channel support: WebUI + QQ

## Quick Start

### Install

```bash
# Python 3.12 + backend deps
uv sync

# Optional: enable ImageRenderer (Playwright + Chromium)
playwright install chromium

# Web deps
cd web && npm install && cd ..
```

### Configure

```bash
cp config/secrets.yaml.example config/secrets.yaml
# Edit config/secrets.yaml to fill in API keys and (optional) email / QQ configs.
```

### Run

```bash
# Production default: 8765
uv run python -m hypo_agent

# Experimental port: 8766
uv run python -m hypo_agent --port 8766

# Default smoke path: test mode (isolated sandbox data, QQ adapter disabled)
bash test_run.sh
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

## Environment Variables

- `HYPO_PORT` (default: `8765`)
- `HYPO_MEMORY_DIR` (default: `./memory`)

## Repo Layout

- `src/`: Python backend (`hypo_agent/`)
- `web/`: WebUI (PWA) + dashboard
- `config/`: YAML config files (`models.yaml`, `security.yaml`, `tasks.yaml`, `secrets.yaml`, etc.)
- `memory/`: runtime data (default; can be externalized via `HYPO_MEMORY_DIR`)
- `docs/`: architecture, plans, runbooks
- `scripts/`: CLI + utilities

## Development

```bash
uv run pytest -q
cd web && npm run test && cd ..
bash test_run.sh
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

说明：

- 默认不要对部署中的 `8765` 实例直接跑 smoke。
- `test_run.sh` 会启用 `HYPO_TEST_MODE=1`，数据写入 `test/sandbox/`，且不会注册 QQ adapter。
- `ImageRenderer` 依赖 Playwright Chromium；未安装时应用仍可启动，但图片渲染功能会显示为 unavailable。

## Port Convention

- Production: `8765`
- Experimental: `8766`
