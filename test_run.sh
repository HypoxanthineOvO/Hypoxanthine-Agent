#!/bin/bash
set -euo pipefail

export HYPO_TEST_MODE=1

exec uv run python -m hypo_agent "$@"
