#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Defaults for development
export HYPO_PORT="${HYPO_PORT:-8766}"
export HYPO_MEMORY_DIR="${HYPO_MEMORY_DIR:-./memory}"

mkdir -p "$HYPO_MEMORY_DIR"

if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$ROOT_DIR/src"
else
  export PYTHONPATH="$ROOT_DIR/src:$PYTHONPATH"
fi

echo "Starting Hypo-Agent (dev) on port $HYPO_PORT with reload..."
exec python -m uvicorn hypo_agent.gateway.main:build_app \
  --factory \
  --reload \
  --reload-dir "$ROOT_DIR/src" \
  --host "0.0.0.0" \
  --port "$HYPO_PORT" \
  --log-level "info"

