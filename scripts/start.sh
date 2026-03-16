#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_NAME="Hypo-Agent"
UV_BIN="${HYPO_UV_BIN:-uv}"

RUN_DIR="${HYPO_RUN_DIR:-$ROOT_DIR/run}"
LOG_DIR="${HYPO_LOG_DIR:-$ROOT_DIR/logs}"
WEB_DIR="${HYPO_WEB_DIR:-$ROOT_DIR/web}"
MEMORY_DIR="${HYPO_MEMORY_DIR:-$ROOT_DIR/memory}"
RUNTIME_STATE_FILE="$RUN_DIR/hypo-agent-runtime.env"

BACKEND_HOST="${HYPO_BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${HYPO_BACKEND_PORT:-8765}"
FRONTEND_HOST="${HYPO_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${HYPO_FRONTEND_PORT:-5173}"

STARTUP_TIMEOUT="${HYPO_STARTUP_TIMEOUT:-20}"
STOP_TIMEOUT="${HYPO_STOP_TIMEOUT:-15}"

BACKEND_PID_FILE="$RUN_DIR/hypo-agent-backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/hypo-agent-frontend.pid"
BACKEND_LOG_FILE="$LOG_DIR/backend.log"
FRONTEND_LOG_FILE="$LOG_DIR/frontend.log"

BACKEND_URL=""
FRONTEND_URL=""

usage() {
  cat <<EOF
Usage:
  bash scripts/start.sh <command> [args]

Commands:
  start             Start backend and frontend in the background
  stop              Stop backend and frontend gracefully
  restart           Restart backend and frontend
  status            Show process and port status
  logs [target]     Tail logs: backend (default), frontend, or all
  --help, help      Show this help message

Options for start/restart:
  --backend-port PORT   Override backend port for this launch
  --frontend-port PORT  Override frontend port for this launch

Defaults:
  Backend:  ${BACKEND_URL}
  Frontend: ${FRONTEND_URL}
  Runtime:  ${UV_BIN} run
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "$*"
}

ensure_dirs() {
  mkdir -p "$RUN_DIR" "$LOG_DIR" "$MEMORY_DIR"
  touch "$BACKEND_LOG_FILE" "$FRONTEND_LOG_FILE"
}

refresh_urls() {
  BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
  FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
}

save_runtime_config() {
  cat >"$RUNTIME_STATE_FILE" <<EOF
BACKEND_PORT=${BACKEND_PORT}
FRONTEND_PORT=${FRONTEND_PORT}
EOF
}

load_runtime_config() {
  if [[ ! -f "$RUNTIME_STATE_FILE" ]]; then
    return 0
  fi

  while IFS='=' read -r key value; do
    case "$key" in
      BACKEND_PORT)
        [[ -n "$value" ]] && BACKEND_PORT="$value"
        ;;
      FRONTEND_PORT)
        [[ -n "$value" ]] && FRONTEND_PORT="$value"
        ;;
    esac
  done <"$RUNTIME_STATE_FILE"
}

validate_port() {
  local name="$1"
  local port="$2"
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    die "invalid ${name} port '${port}'. Expected 1-65535."
  fi
}

apply_start_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --backend-port)
        [[ $# -ge 2 ]] || die "missing value for --backend-port"
        BACKEND_PORT="$2"
        shift 2
        ;;
      --frontend-port)
        [[ $# -ge 2 ]] || die "missing value for --frontend-port"
        FRONTEND_PORT="$2"
        shift 2
        ;;
      --help|help)
        usage
        exit 0
        ;;
      *)
        die "unknown option '$1'."
        ;;
    esac
  done

  validate_port "backend" "$BACKEND_PORT"
  validate_port "frontend" "$FRONTEND_PORT"
  refresh_urls
}

read_pid() {
  local pid_file="$1"
  tr -d '[:space:]' <"$pid_file"
}

pid_is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

clear_stale_pid() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi

  local pid
  pid="$(read_pid "$pid_file")"
  if ! pid_is_running "$pid"; then
    rm -f "$pid_file"
  fi
}

ss_listener() {
  local port="$1"
  ss -ltnpH 2>/dev/null | awk -v port="$port" '
    $1 == "LISTEN" && $4 ~ (":" port "$") {
      print
      found = 1
    }
    END {
      if (!found) {
        exit 1
      }
    }
  '
}

lsof_listener() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null
}

listener_info() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss_listener "$port"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof_listener "$port"
    return $?
  fi
  return 2
}

port_is_in_use() {
  local port="$1"
  if listener_info "$port" >/dev/null; then
    return 0
  fi
  return 1
}

require_port_free() {
  local name="$1"
  local port="$2"
  if port_is_in_use "$port"; then
    echo "ERROR: ${name} port ${port} is already in use." >&2
    listener_info "$port" >&2 || true
    exit 1
  fi
}

resolve_uv() {
  if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    die "uv not found in PATH."
  fi
  UV_BIN="$(command -v "$UV_BIN")"
}

wait_for_service() {
  local name="$1"
  local pid="$2"
  local port="$3"
  local log_file="$4"
  local waited=0

  while (( waited < STARTUP_TIMEOUT )); do
    if ! pid_is_running "$pid"; then
      echo "ERROR: ${name} exited before becoming ready. Check ${log_file}." >&2
      return 1
    fi
    if port_is_in_use "$port"; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done

  echo "ERROR: ${name} did not open port ${port} within ${STARTUP_TIMEOUT}s. Check ${log_file}." >&2
  return 1
}

start_backend() {
  nohup env \
    ROOT_DIR="$ROOT_DIR" \
    UV_BIN="$UV_BIN" \
    MEMORY_DIR="$MEMORY_DIR" \
    BACKEND_HOST="$BACKEND_HOST" \
    BACKEND_PORT="$BACKEND_PORT" \
    bash -lc '
      set -euo pipefail
      cd "$ROOT_DIR"
      export HYPO_PORT="$BACKEND_PORT"
      export HYPO_MEMORY_DIR="$MEMORY_DIR"
      exec "$UV_BIN" run python -m hypo_agent \
        --host "$BACKEND_HOST" \
        --port "$BACKEND_PORT"
    ' >>"$BACKEND_LOG_FILE" 2>&1 &
  echo "$!" >"$BACKEND_PID_FILE"
}

start_frontend() {
  nohup env \
    WEB_DIR="$WEB_DIR" \
    FRONTEND_HOST="$FRONTEND_HOST" \
    FRONTEND_PORT="$FRONTEND_PORT" \
    bash -lc '
      set -euo pipefail
      cd "$WEB_DIR"
      setsid npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort &
      child_pid="$!"
      cleanup() {
        kill -TERM "-${child_pid}" >/dev/null 2>&1 || true
      }
      trap cleanup TERM INT
      wait "$child_pid"
    ' >>"$FRONTEND_LOG_FILE" 2>&1 &
  echo "$!" >"$FRONTEND_PID_FILE"
}

stop_service() {
  local name="$1"
  local pid_file="$2"

  clear_stale_pid "$pid_file"
  if [[ ! -f "$pid_file" ]]; then
    info "${name} not running."
    return 0
  fi

  local pid
  pid="$(read_pid "$pid_file")"
  if ! pid_is_running "$pid"; then
    rm -f "$pid_file"
    info "${name} not running."
    return 0
  fi

  kill "$pid" >/dev/null 2>&1 || true

  local waited=0
  while pid_is_running "$pid" && (( waited < STOP_TIMEOUT )); do
    sleep 1
    waited=$((waited + 1))
  done

  if pid_is_running "$pid"; then
    echo "ERROR: ${name} did not stop within ${STOP_TIMEOUT}s." >&2
    return 1
  fi

  rm -f "$pid_file"
  info "${name} stopped."
}

print_service_status() {
  local name="$1"
  local pid_file="$2"
  local port="$3"
  local url="$4"

  clear_stale_pid "$pid_file"

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(read_pid "$pid_file")"
    if pid_is_running "$pid"; then
      echo "${name}: running (PID ${pid})"
    else
      echo "${name}: stopped (stale PID file was cleared)"
      rm -f "$pid_file"
    fi
  else
    echo "${name}: stopped"
  fi

  local listener_output
  if listener_output="$(listener_info "$port" 2>/dev/null)"; then
    echo "Port ${port}: listening"
    echo "$listener_output"
  else
    echo "Port ${port}: not listening"
  fi
  echo "URL: ${url}"
}

show_logs() {
  local target="${1:-backend}"
  ensure_dirs

  case "$target" in
    backend)
      exec tail -n 50 -f "$BACKEND_LOG_FILE"
      ;;
    frontend)
      exec tail -n 50 -f "$FRONTEND_LOG_FILE"
      ;;
    all)
      exec tail -n 50 -f "$BACKEND_LOG_FILE" "$FRONTEND_LOG_FILE"
      ;;
    *)
      die "unknown logs target '$target'. Use backend, frontend, or all."
      ;;
  esac
}

start_all() {
  ensure_dirs
  resolve_uv
  refresh_urls

  [[ -d "$WEB_DIR" ]] || die "frontend directory not found: $WEB_DIR"

  clear_stale_pid "$BACKEND_PID_FILE"
  clear_stale_pid "$FRONTEND_PID_FILE"

  [[ ! -f "$BACKEND_PID_FILE" ]] || die "backend is already running. Use status or restart."
  [[ ! -f "$FRONTEND_PID_FILE" ]] || die "frontend is already running. Use status or restart."

  require_port_free "backend" "$BACKEND_PORT"
  require_port_free "frontend" "$FRONTEND_PORT"

  info "Starting ${PROJECT_NAME} backend..."
  start_backend
  local backend_pid
  backend_pid="$(read_pid "$BACKEND_PID_FILE")"
  if ! wait_for_service "backend" "$backend_pid" "$BACKEND_PORT" "$BACKEND_LOG_FILE"; then
    rm -f "$BACKEND_PID_FILE"
    exit 1
  fi

  info "Starting ${PROJECT_NAME} frontend..."
  start_frontend
  local frontend_pid
  frontend_pid="$(read_pid "$FRONTEND_PID_FILE")"
  if ! wait_for_service "frontend" "$frontend_pid" "$FRONTEND_PORT" "$FRONTEND_LOG_FILE"; then
    stop_service "backend" "$BACKEND_PID_FILE" || true
    rm -f "$FRONTEND_PID_FILE"
    exit 1
  fi

  save_runtime_config

  cat <<EOF
Backend PID: ${backend_pid}
Backend port: ${BACKEND_PORT}
Backend URL: ${BACKEND_URL}
Frontend PID: ${frontend_pid}
Frontend port: ${FRONTEND_PORT}
Frontend URL: ${FRONTEND_URL}
Logs:
  backend -> ${BACKEND_LOG_FILE}
  frontend -> ${FRONTEND_LOG_FILE}
EOF
}

stop_all() {
  ensure_dirs
  refresh_urls
  local exit_code=0

  stop_service "frontend" "$FRONTEND_PID_FILE" || exit_code=1
  stop_service "backend" "$BACKEND_PID_FILE" || exit_code=1

  return "$exit_code"
}

status_all() {
  ensure_dirs
  refresh_urls
  print_service_status "backend" "$BACKEND_PID_FILE" "$BACKEND_PORT" "$BACKEND_URL"
  echo
  print_service_status "frontend" "$FRONTEND_PID_FILE" "$FRONTEND_PORT" "$FRONTEND_URL"
}

refresh_urls

COMMAND="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi
case "$COMMAND" in
  start)
    apply_start_options "$@"
    start_all
    ;;
  stop)
    [[ $# -eq 0 ]] || die "stop does not accept extra arguments."
    load_runtime_config
    stop_all
    ;;
  restart)
    load_runtime_config
    apply_start_options "$@"
    stop_all || true
    start_all
    ;;
  status)
    [[ $# -eq 0 ]] || die "status does not accept extra arguments."
    load_runtime_config
    status_all
    ;;
  logs)
    [[ $# -le 1 ]] || die "logs accepts at most one target argument."
    show_logs "${1:-backend}"
    ;;
  --help|help)
    usage
    ;;
  *)
    usage
    die "unknown command '$COMMAND'."
    ;;
esac
