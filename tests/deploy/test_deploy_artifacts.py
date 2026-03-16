from __future__ import annotations

from pathlib import Path


def test_systemd_service_has_required_runtime_settings() -> None:
    content = Path("deploy/hypo-agent.service").read_text(encoding="utf-8")

    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert "Type=simple" in content
    assert "WorkingDirectory=/home/heyx/Hypo-Agent" in content
    assert "Environment=PYTHONPATH=/home/heyx/Hypo-Agent/src" in content
    assert "Environment=HYPO_PORT=8765" in content
    assert "ExecStart=/usr/bin/env bash -lc 'uv run python -m hypo_agent" in content
    assert "--host 127.0.0.1" in content
    assert "--port 8765" in content
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content
    assert "StandardOutput=journal" in content
    assert "StandardError=journal" in content
    assert "WantedBy=multi-user.target" in content


def test_install_script_covers_build_systemd_and_nginx_steps() -> None:
    content = Path("deploy/install.sh").read_text(encoding="utf-8")

    assert "npm run build" in content
    assert "ln -sf" in content
    assert "/etc/systemd/system" in content
    assert "systemctl daemon-reload" in content
    assert "sites-enabled" in content
    assert "conf.d" in content
    assert "nginx -t" in content
    assert "systemctl enable --now hypo-agent" in content
    assert "systemctl reload nginx" in content
    assert 'read -r -p "Proceed with service enable and nginx reload? [y/N] "' in content


def test_deploy_readme_covers_core_ops_commands() -> None:
    content = Path("deploy/README.md").read_text(encoding="utf-8")

    assert "Quick Start" in content
    assert "bash deploy/install.sh" in content
    assert "systemctl start hypo-agent" in content
    assert "systemctl status hypo-agent" in content
    assert "journalctl -u hypo-agent -f" in content
    assert "Nginx" in content
    assert "uv sync" in content
