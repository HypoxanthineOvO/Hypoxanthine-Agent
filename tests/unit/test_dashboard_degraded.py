from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from hypo_agent.gateway import dashboard_api


class _HealthyQQBotService:
    def get_status(self) -> dict[str, object]:
        return {
            "status": "enabled",
            "qq_bot_enabled": True,
            "qq_bot_app_id": "••••1234",
            "ws_connected": False,
            "connected_at": None,
            "last_message_at": None,
            "messages_received": 0,
            "messages_sent": 0,
        }


class _BrokenQQBotClient:
    def get_status(self) -> dict[str, object]:
        raise RuntimeError("qq transport probe failed")

    async def stop(self) -> None:
        return None


@pytest.mark.unit
def test_channels_status_logs_degraded_when_qq_bot_config_load_fails(app_factory, monkeypatch) -> None:
    app = app_factory()

    def _broken_loader(path):
        del path
        raise ValueError("invalid secrets payload")

    monkeypatch.setattr(dashboard_api, "load_secrets_config", _broken_loader)

    with capture_logs() as logs:
        with TestClient(app) as client:
            response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    assert response.json()["channels"]["qq_bot"]["status"] == "disabled"
    assert any(entry["event"] == "dashboard.channel_status.degraded" for entry in logs)


@pytest.mark.unit
def test_channels_status_logs_degraded_when_qq_bot_transport_status_fails(app_factory) -> None:
    app = app_factory(
        config_overrides={
            "skills": {
                "skills": {
                    "qq": {"enabled": False},
                    "qq_bot": {"enabled": True},
                }
            },
            "secrets": {
                "services": {
                    "qq_bot": {
                        "enabled": True,
                        "app_id": "1029384756",
                        "app_secret": "bot-secret",
                    }
                }
            },
        }
    )

    with capture_logs() as logs:
        with TestClient(app) as client:
            client.app.state.qq_bot_channel_service = _HealthyQQBotService()
            client.app.state.qq_ws_client = _BrokenQQBotClient()
            response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["qq_bot"]["qq_bot_enabled"] is True
    assert payload["channels"]["qq_bot"]["ws_connected"] is False
    assert any(entry["event"] == "dashboard.token_stats.degraded" for entry in logs)


@pytest.mark.unit
def test_channels_status_logs_degraded_when_weixin_config_load_fails(app_factory, monkeypatch) -> None:
    app = app_factory()
    calls = {"count": 0}

    def _loader(path):
        del path
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(services=SimpleNamespace(qq_bot=None, weixin=None))
        raise RuntimeError("weixin config unavailable")

    monkeypatch.setattr(dashboard_api, "load_secrets_config", _loader)

    with capture_logs() as logs:
        with TestClient(app) as client:
            response = client.get("/api/channels/status", params={"token": "test-token"})

    assert response.status_code == 200
    assert response.json()["channels"]["weixin"]["status"] == "disabled"
    assert any(entry["event"] == "dashboard.system_info.degraded" for entry in logs)
