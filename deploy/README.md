# Deploy

Production deployment uses `systemd` for the FastAPI backend and Nginx for the built frontend bundle. The development helper [scripts/start.sh](/home/heyx/Hypo-Agent/scripts/start.sh) stays available, but it is not part of production startup.

## Quick Start

1. Run `bash deploy/install.sh`
2. Sync Python dependencies with `uv sync`
3. Start or verify the backend service with `sudo systemctl start hypo-agent`
4. Open `http://127.0.0.1:8080/` in a browser

## Common Commands

- `sudo systemctl start hypo-agent`
- `sudo systemctl stop hypo-agent`
- `sudo systemctl restart hypo-agent`
- `sudo systemctl status hypo-agent`
- `journalctl -u hypo-agent -f`
- `sudo nginx -t`
- `sudo systemctl reload nginx`

## Layout

- `deploy/hypo-agent.service`: systemd unit for the FastAPI backend
- `deploy/nginx/hypo-agent.conf`: Nginx site config serving `web/dist` and proxying `/api/` plus `/ws`
- `deploy/install.sh`: one-shot installer for frontend build, symlinks, daemon reload, and optional enable/start
- `deploy/README.md`: deployment notes and troubleshooting

## Troubleshooting

### Port Conflicts

- Backend service listens on `127.0.0.1:8765`
- Nginx in this repo is configured for `8080` because port `80` was already occupied on the target host during setup
- Check listeners with `ss -ltnp | rg ':(8765|8080)'`

### Nginx Errors

- Validate config with `sudo nginx -t`
- If `/etc/nginx/sites-enabled` does not exist, the installer falls back to `/etc/nginx/conf.d`
- If Nginx is not installed, install it first and rerun `bash deploy/install.sh`

### Python Environment

- The systemd unit launches `uv run python -m hypo_agent` from the project root
- Run `uv sync` after pulling new dependencies or updating `uv.lock`
- `PYTHONPATH` is set to `/home/heyx/Hypo-Agent/src` inside the unit so local source is used

### Backend Health Checks

- There is no dedicated unauthenticated `/api/health` endpoint in the current backend
- For authenticated checks, use an existing API endpoint such as `/api/dashboard/status?token=<your-token>`
