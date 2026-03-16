#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

UV_BIN="${HYPO_UV_BIN:-uv}"
command -v "$UV_BIN" >/dev/null 2>&1 || {
  echo "ERROR: uv not found in PATH." >&2
  exit 1
}
UV_BIN="$(command -v "$UV_BIN")"

# Defaults for development
export HYPO_PORT="${HYPO_PORT:-8766}"
export HYPO_MEMORY_DIR="${HYPO_MEMORY_DIR:-./memory}"

mkdir -p "$HYPO_MEMORY_DIR"

echo "Starting Hypo-Agent (dev) on port $HYPO_PORT with reload..."
exec "$UV_BIN" run python -m uvicorn hypo_agent.gateway.main:build_app \
  --factory \
  --reload \
  --reload-dir "$ROOT_DIR/src" \
  --host "0.0.0.0" \
  --port "$HYPO_PORT" \
  --log-level "info"
