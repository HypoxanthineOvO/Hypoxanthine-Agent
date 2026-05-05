from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import create_app


def test_outbound_send_api_requires_token() -> None:
    app = create_app(auth_token="test-token")
    client = TestClient(app)

    response = client.post("/api/outbound/send", json={"text": "hello", "dry_run": True})

    assert response.status_code == 401


def test_outbound_send_api_returns_dry_run_plan() -> None:
    app = create_app(auth_token="test-token")
    client = TestClient(app)

    response = client.post(
        "/api/outbound/send",
        headers={"Authorization": "Bearer test-token"},
        json={"text": "[C3-SMOKE] hello", "channels": ["qq"], "dry_run": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["target_channels"] == ["qq"]
    assert payload["channel_results"]["qq"]["planned"] is True
