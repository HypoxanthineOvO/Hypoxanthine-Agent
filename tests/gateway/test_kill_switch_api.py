from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import create_app


class DummyPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}


def test_kill_switch_api_toggles_global_state() -> None:
    app = create_app(auth_token="test-token", pipeline=DummyPipeline())

    with TestClient(app) as client:
        enabled_response = client.post("/api/kill-switch", json={"enabled": True})
        assert enabled_response.status_code == 200
        assert enabled_response.json() == {"enabled": True}
        assert client.app.state.deps.circuit_breaker.get_global_kill_switch() is True

        disabled_response = client.post("/api/kill-switch", json={"enabled": False})
        assert disabled_response.status_code == 200
        assert disabled_response.json() == {"enabled": False}
        assert client.app.state.deps.circuit_breaker.get_global_kill_switch() is False
