#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Defaults
export HYPO_PORT="${HYPO_PORT:-8765}"
export HYPO_MEMORY_DIR="${HYPO_MEMORY_DIR:-./memory}"

# Ensure memory directory exists (runtime data should not pollute the repo)
mkdir -p "$HYPO_MEMORY_DIR"

# Ensure we run the local src/ code even if another hypo-agent is installed globally.
if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$ROOT_DIR/src"
else
  export PYTHONPATH="$ROOT_DIR/src:$PYTHONPATH"
fi

echo "Starting Hypo-Agent on port $HYPO_PORT..."
exec python -m hypo_agent.gateway.main

