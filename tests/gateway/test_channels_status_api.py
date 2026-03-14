from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import CircuitBreakerConfig, DirectoryWhitelist
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.security.permission_manager import PermissionManager


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class DummyEmailSkill:
    def get_status(self, *, scheduler=None):
        del scheduler
        return {
            "status": "enabled",
            "accounts": ["hyx021203@shanghaitech.edu.cn"],
            "last_scan_at": "2026-03-11T20:00:00+08:00",
            "next_scan_at": "2026-03-11T20:15:00+08:00",
            "emails_processed": 15,
        }


class DummySkillManager:
    def __init__(self, include_email: bool = True) -> None:
        self._skills = {"email_scanner": DummyEmailSkill()} if include_email else {}

    def list_skills(self) -> list[dict]:
        return []


class DummyQQClient:
    def get_status(self) -> dict:
        return {
            "status": "connected",
            "bot_qq": "3637647606",
            "napcat_ws_url": "ws://127.0.0.1:3009/onebot/v11/ws",
            "connected_at": "2026-03-11T15:07:18+08:00",
            "last_message_at": "2026-03-11T20:45:00+08:00",
            "messages_received": 42,
            "messages_sent": 38,
        }

    async def stop(self) -> None:
        return None


class DummyHeartbeatService:
    def get_status(self, *, scheduler=None):
        del scheduler
        return {
            "status": "running",
            "last_heartbeat_at": "2026-03-11T20:50:00+08:00",
            "active_tasks": 2,
        }


class DummyWsManager:
    def get_status(self) -> dict:
        return {
            "status": "connected",
            "active_connections": 1,
            "last_message_at": "2026-03-11T20:30:00+08:00",
        }


def _build_client(tmp_path: Path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        permission_manager=PermissionManager(
            DirectoryWhitelist.model_validate({"rules": [], "default_policy": "readonly"})
        ),
        skill_manager=DummySkillManager(),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app)


def test_channels_status_returns_runtime_structure(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  qq:
    enabled: true
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        client.app.state.qq_ws_client = DummyQQClient()
        client.app.state.qq_channel_service = object()
        client.app.state.heartbeat_service = DummyHeartbeatService()
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["webui"]["status"] == "connected"
    assert payload["channels"]["webui"]["active_connections"] == 1
    assert payload["channels"]["qq"]["status"] == "connected"
    assert payload["channels"]["qq"]["messages_received"] == 42
    assert payload["channels"]["email"]["status"] == "enabled"
    assert payload["channels"]["email"]["accounts"] == ["hyx021203@shanghaitech.edu.cn"]
    assert payload["channels"]["heartbeat"]["status"] == "running"
    assert payload["channels"]["heartbeat"]["active_tasks"] == 2


def test_channels_status_returns_disabled_when_qq_not_enabled(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  qq:
    enabled: false
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["qq"]["status"] == "disabled"


def test_channels_status_returns_disabled_when_email_not_enabled(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    client.app.state.deps.skill_manager = DummySkillManager(include_email=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  qq:
    enabled: true
  email_scanner:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["email"]["status"] == "disabled"
