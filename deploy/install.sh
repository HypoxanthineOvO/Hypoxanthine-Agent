#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy"
WEB_DIR="$ROOT_DIR/web"
SERVICE_SOURCE="$DEPLOY_DIR/hypo-agent.service"
NGINX_SOURCE="$DEPLOY_DIR/nginx/hypo-agent.conf"
SYSTEMD_TARGET="/etc/systemd/system/hypo-agent.service"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "$*"
}

run_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required for installation."
  fi

  if ! sudo -n true >/dev/null 2>&1; then
    die "passwordless sudo or a pre-authenticated sudo session is required. Run 'sudo -v' first."
  fi

  sudo "$@"
}

resolve_nginx_target_dir() {
  if [[ -d /etc/nginx/sites-enabled ]]; then
    printf '%s\n' "/etc/nginx/sites-enabled"
    return 0
  fi
  if [[ -d /etc/nginx/conf.d ]]; then
    printf '%s\n' "/etc/nginx/conf.d"
    return 0
  fi
  die "neither /etc/nginx/sites-enabled nor /etc/nginx/conf.d exists."
}

require_commands() {
  command -v uv >/dev/null 2>&1 || die "uv is required."
  command -v npm >/dev/null 2>&1 || die "npm is required."
  command -v systemctl >/dev/null 2>&1 || die "systemctl is required."
  command -v nginx >/dev/null 2>&1 || die "nginx is required but not installed."
}

main() {
  require_commands

  info "Syncing Python environment..."
  uv sync

  info "Building frontend..."
  npm run build --prefix "$WEB_DIR"

  local nginx_target_dir
  nginx_target_dir="$(resolve_nginx_target_dir)"
  local nginx_target="$nginx_target_dir/hypo-agent.conf"

  info "Linking systemd service..."
  run_sudo ln -sf "$SERVICE_SOURCE" "$SYSTEMD_TARGET"

  info "Reloading systemd daemon..."
  run_sudo systemctl daemon-reload

  info "Linking nginx site config..."
  run_sudo ln -sf "$NGINX_SOURCE" "$nginx_target"

  info "Validating nginx config..."
  run_sudo nginx -t

  cat <<EOF
Install preparation complete.

Systemd unit: $SYSTEMD_TARGET
Nginx config: $nginx_target
Project root: $ROOT_DIR

Next actions:
  sudo systemctl enable --now hypo-agent
  sudo systemctl reload nginx
EOF

  read -r -p "Proceed with service enable and nginx reload? [y/N] " confirm
  case "$confirm" in
    [yY]|[yY][eE][sS])
      run_sudo systemctl enable --now hypo-agent
      run_sudo systemctl reload nginx
      info "hypo-agent enabled and nginx reloaded."
      ;;
    *)
      info "Skipped service enable and nginx reload."
      ;;
  esac
}

main "$@"
