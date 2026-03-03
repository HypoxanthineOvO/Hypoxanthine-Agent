from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import create_app


class DummyPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}


def test_sessions_api_supports_browser_cors_preflight() -> None:
    app = create_app(auth_token="test-token", pipeline=DummyPipeline())
    with TestClient(app) as client:
        response = client.options(
            "/api/sessions",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
