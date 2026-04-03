from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message
from tests.shared import DummyPipeline


def _build_client(tmp_path: Path) -> TestClient:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    app.state.knowledge_dir = knowledge_dir
    return TestClient(app)


def test_memory_tables_require_token(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        response = client.get("/api/memory/tables")
    assert response.status_code == 401


def test_memory_tables_list_and_rows(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        asyncio.run(client.app.state.structured_store.set_preference("language", "zh-CN"))

        tables = client.get("/api/memory/tables", params={"token": "test-token"})
        rows = client.get(
            "/api/memory/tables/preferences",
            params={"token": "test-token", "page": 1, "size": 50},
        )

    assert tables.status_code == 200
    names = [item["name"] for item in tables.json()["tables"]]
    assert "preferences" in names
    assert rows.status_code == 200
    payload = rows.json()
    assert payload["table"] == "preferences"
    assert payload["rows"][0]["pref_key"] == "language"


def test_memory_tables_put_allows_preferences_only(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        asyncio.run(client.app.state.structured_store.set_preference("timezone", "UTC"))

        denied = client.put(
            "/api/memory/tables/sessions/s1",
            params={"token": "test-token"},
            json={"values": {"updated_at": "2026-03-06T00:00:00Z"}},
        )
        allowed = client.put(
            "/api/memory/tables/preferences/timezone",
            params={"token": "test-token"},
            json={"values": {"pref_value": "Asia/Shanghai"}},
        )
        verify = client.get(
            "/api/memory/tables/preferences",
            params={"token": "test-token"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert verify.status_code == 200
    assert verify.json()["rows"][0]["pref_value"] == "Asia/Shanghai"


def test_memory_files_list_get_put_and_path_validation(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        put_resp = client.put(
            "/api/memory/files/notes/test.md",
            params={"token": "test-token"},
            json={"content": "# hello"},
        )
        list_resp = client.get("/api/memory/files", params={"token": "test-token"})
        get_resp = client.get(
            "/api/memory/files/notes/test.md",
            params={"token": "test-token"},
        )
        invalid = client.get(
            "/api/memory/files/%2E%2E/%2E%2E/etc/passwd",
            params={"token": "test-token"},
        )

    assert put_resp.status_code == 200
    assert list_resp.status_code == 200
    assert "notes/test.md" in list_resp.json()["files"]
    assert get_resp.status_code == 200
    assert get_resp.json()["content"] == "# hello"
    assert invalid.status_code == 400


def test_session_export_json_and_markdown(tmp_path) -> None:
    client = _build_client(tmp_path)
    with client:
        client.app.state.session_memory.append(
            Message(
                text="hello",
                sender="user",
                session_id="s1",
                timestamp=datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
            )
        )
        client.app.state.session_memory.append(
            Message(
                text="world",
                sender="assistant",
                session_id="s1",
                timestamp=datetime(2026, 3, 6, 10, 1, tzinfo=UTC),
            )
        )

        json_resp = client.get(
            "/api/sessions/s1/export",
            params={"token": "test-token", "format": "json"},
        )
        markdown_resp = client.get(
            "/api/sessions/s1/export",
            params={"token": "test-token", "format": "markdown"},
        )

    assert json_resp.status_code == 200
    assert len(json_resp.json()["messages"]) == 2
    assert markdown_resp.status_code == 200
    assert "# Session s1" in markdown_resp.text
