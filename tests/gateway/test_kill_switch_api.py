from __future__ import annotations

from fastapi.testclient import TestClient

from tests.shared import DummyPipeline


def test_kill_switch_api_toggles_global_state(app_factory) -> None:
    app = app_factory(pipeline=DummyPipeline())

    with TestClient(app) as client:
        enabled_response = client.post("/api/kill-switch", json={"enabled": True})
        assert enabled_response.status_code == 200
        assert enabled_response.json() == {"enabled": True}
        assert client.app.state.deps.circuit_breaker.get_global_kill_switch() is True

        disabled_response = client.post("/api/kill-switch", json={"enabled": False})
        assert disabled_response.status_code == 200
        assert disabled_response.json() == {"enabled": False}
        assert client.app.state.deps.circuit_breaker.get_global_kill_switch() is False
