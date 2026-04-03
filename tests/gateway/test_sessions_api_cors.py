from __future__ import annotations

from fastapi.testclient import TestClient

from tests.shared import DummyPipeline


def test_sessions_api_supports_browser_cors_preflight(app_factory) -> None:
    app = app_factory(pipeline=DummyPipeline())
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
