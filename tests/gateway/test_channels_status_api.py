from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import CircuitBreakerConfig, DirectoryWhitelist
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.security.permission_manager import PermissionManager
from tests.shared import DummyPipeline


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


class DummyQQService:
    def __init__(self, *, online: bool | None) -> None:
        self._online = online

    def get_runtime_status(self) -> dict:
        return {
            "online": self._online,
            "good": True if self._online is not None else None,
        }


class DummyQQBotService:
    def get_status(self) -> dict:
        return {
            "status": "connected",
            "qq_bot_enabled": True,
            "qq_bot_app_id": "••••4756",
            "ws_connected": True,
            "connected_at": "2026-03-26T10:40:00+08:00",
            "last_message_at": "2026-03-26T10:45:00+08:00",
            "messages_received": 3,
            "messages_sent": 2,
        }


class DummyHeartbeatService:
    def get_status(self, *, scheduler=None):
        del scheduler
        return {
            "status": "running",
            "last_heartbeat_at": "2026-03-11T20:50:00+08:00",
            "active_tasks": 2,
        }


class DummyWeixinChannel:
    def get_status(self) -> dict:
        return {
            "status": "connected",
            "bot_id": "wx-bot-1",
            "user_id": "target@im.wechat",
            "last_message_at": "2026-03-11T20:40:00+08:00",
            "messages_received": 12,
            "messages_sent": 9,
        }

    async def stop(self) -> None:
        return None


class DummyFeishuChannel:
    def get_status(self) -> dict:
        return {
            "status": "connected",
            "app_id": "••••ishu",
            "chat_count": 2,
            "last_message_at": "2026-03-11T20:42:00+08:00",
            "messages_received": 5,
            "messages_sent": 4,
        }

    async def stop(self) -> None:
        return None


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
  qq_bot:
    enabled: true
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq_bot:
    app_id: "1029384756"
    app_secret: "bot-secret-xyz"
    enabled: true
  weixin:
    enabled: true
    token_path: memory/weixin_auth.json
    allowed_users: []
  feishu:
    app_id: "cli_test_ishu"
    app_secret: "secret_test"
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "config.yaml").write_text(
        """
channels:
  feishu:
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        client.app.state.qq_bot_channel_service = DummyQQBotService()
        client.app.state.qq_channel_service = client.app.state.qq_bot_channel_service
        client.app.state.heartbeat_service = DummyHeartbeatService()
        client.app.state.weixin_channel = DummyWeixinChannel()
        client.app.state.feishu_channel = DummyFeishuChannel()
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["webui"]["status"] == "connected"
    assert payload["channels"]["webui"]["active_connections"] == 1
    assert payload["channels"]["qq_bot"]["status"] == "connected"
    assert payload["channels"]["qq_bot"]["qq_bot_enabled"] is True
    assert payload["channels"]["qq_bot"]["qq_bot_app_id"] == "••••4756"
    assert payload["channels"]["qq_bot"]["ws_connected"] is True
    assert payload["channels"]["weixin"]["status"] == "connected"
    assert payload["channels"]["weixin"]["bot_id"] == "wx-bot-1"
    assert payload["channels"]["feishu"]["status"] == "connected"
    assert payload["channels"]["feishu"]["app_id"] == "••••ishu"
    assert payload["channels"]["feishu"]["chat_count"] == 2
    assert payload["channels"]["qq_bot"]["messages_received"] == 3
    assert payload["channels"]["email"]["status"] == "enabled"
    assert payload["channels"]["email"]["accounts"] == ["hyx021203@shanghaitech.edu.cn"]
    assert payload["channels"]["heartbeat"]["status"] == "running"
    assert payload["channels"]["heartbeat"]["active_tasks"] == 2
    assert "qq_napcat" not in payload["channels"]


def test_channels_status_returns_disabled_when_qq_not_enabled(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  qq_bot:
    enabled: false
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text("providers: {}\nservices: {}\n", encoding="utf-8")

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["qq_bot"]["status"] == "disabled"
    assert "qq_napcat" not in payload["channels"]
    assert payload["channels"]["weixin"]["status"] == "disabled"


def test_channels_status_prefers_qq_bot_config_over_legacy_skill_flag(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  qq_bot:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq_bot:
    app_id: "1029384756"
    app_secret: "bot-secret-xyz"
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
    assert payload["channels"]["qq_bot"]["qq_bot_enabled"] is True
    assert payload["channels"]["qq_bot"]["qq_bot_app_id"] == "••••4756"
    assert payload["channels"]["qq_bot"]["status"] == "enabled"
    assert "qq_napcat" not in payload["channels"]


def test_channels_status_returns_disabled_when_email_not_enabled(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    client.app.state.deps.skill_manager = DummySkillManager(include_email=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
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


def test_channels_status_marks_qq_disconnected_when_napcat_reports_offline(tmp_path, monkeypatch) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text("skills: {}\n", encoding="utf-8")
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq:
    napcat_ws_url: "ws://127.0.0.1:6099"
    napcat_http_url: "http://127.0.0.1:3000"
    bot_qq: "123456789"
    allowed_users: []
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("hypo_agent.gateway.dashboard_api.connection_manager", DummyWsManager())

    with client:
        client.app.state.config_dir = config_dir
        client.app.state.qq_ws_client = DummyQQClient()
        client.app.state.qq_channel_service = DummyQQService(online=False)
        response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["qq_napcat"]["status"] == "disconnected"
    assert payload["channels"]["qq_napcat"]["online"] is False
