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
# Python deps
pip install -r requirements.txt
pip install -e .

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
python -m hypo_agent.gateway.main

# Experimental port: 8766
HYPO_PORT=8766 python -m hypo_agent.gateway.main
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
pytest -q
cd web && npm run test && cd ..
python scripts/agent_cli.py smoke
```

## Port Convention

- Production: `8765`
- Experimental: `8766`
