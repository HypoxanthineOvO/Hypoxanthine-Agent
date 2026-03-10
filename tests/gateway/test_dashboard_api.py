from __future__ import annotations

import asyncio
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


def _build_client(tmp_path: Path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        permission_manager=PermissionManager(
            DirectoryWhitelist.model_validate({"rules": [], "default_policy": "readonly"})
        ),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app)


def test_dashboard_status_requires_token(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.get("/api/dashboard/status")
    assert response.status_code == 401


def test_dashboard_status_returns_runtime_payload(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.get(
            "/api/dashboard/status",
            params={"token": "test-token"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_count"] >= 0
    assert isinstance(payload["uptime_seconds"], float)
    assert isinstance(payload["kill_switch"], bool)
    assert isinstance(payload["bwrap_available"], bool)


def test_dashboard_token_latency_recent_and_skills_endpoints(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        asyncio.run(
            client.app.state.structured_store.record_token_usage(
                session_id="s1",
                requested_model="Gemini3Pro",
                resolved_model="Gemini3Pro",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=120.0,
            )
        )
        asyncio.run(
            client.app.state.structured_store.record_tool_invocation(
                session_id="s1",
                tool_name="run_command",
                skill_name="tmux",
                params_json='{"command":"echo hi"}',
                status="success",
                result_summary="ok",
                duration_ms=12.0,
                error_info="",
            )
        )

        token_stats = client.get(
            "/api/dashboard/token-stats",
            params={"token": "test-token", "days": 7},
        )
        latency_stats = client.get(
            "/api/dashboard/latency-stats",
            params={"token": "test-token", "days": 7},
        )
        recent_tasks = client.get(
            "/api/dashboard/recent-tasks",
            params={"token": "test-token", "limit": 20},
        )
        skills = client.get(
            "/api/dashboard/skills",
            params={"token": "test-token"},
        )

    assert token_stats.status_code == 200
    assert token_stats.json()["data"]
    assert latency_stats.status_code == 200
    assert latency_stats.json()["data"]
    assert recent_tasks.status_code == 200
    recent_payload = recent_tasks.json()
    assert recent_payload["limit"] == 20
    assert len(recent_payload["data"]) == 1
    assert skills.status_code == 200
    assert "data" in skills.json()


def test_dashboard_skills_includes_disabled_configured_skill(tmp_path) -> None:
    client = _build_client(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
skills:
  tmux:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    with client:
        client.app.state.config_dir = config_dir
        response = client.get(
            "/api/dashboard/skills",
            params={"token": "test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    row = next(item for item in payload["data"] if item["name"] == "tmux")
    assert row["enabled"] is False
    assert row["status"] == "disabled"


def test_dashboard_stats_pass_since_filter_to_store(tmp_path) -> None:
    client = _build_client(tmp_path)
    captured: dict[str, str | None] = {"token_since": None, "invocation_since": None}

    async def fake_list_token_usage(
        session_id: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, object]]:
        del session_id
        captured["token_since"] = since_iso
        return []

    async def fake_list_tool_invocations(
        session_id: str | None = None,
        limit: int | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, object]]:
        del session_id, limit
        captured["invocation_since"] = since_iso
        return []

    with client:
        client.app.state.structured_store.list_token_usage = fake_list_token_usage
        client.app.state.structured_store.list_tool_invocations = fake_list_tool_invocations

        token_stats = client.get(
            "/api/dashboard/token-stats",
            params={"token": "test-token", "days": 7},
        )
        latency_stats = client.get(
            "/api/dashboard/latency-stats",
            params={"token": "test-token", "days": 7},
        )

    assert token_stats.status_code == 200
    assert latency_stats.status_code == 200
    assert isinstance(captured["token_since"], str)
    assert captured["token_since"]
    assert isinstance(captured["invocation_since"], str)
    assert captured["invocation_since"]
