from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class DummyPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}


def _build_app(tmp_path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app)


def test_get_sessions_returns_session_list(tmp_path) -> None:
    with _build_app(tmp_path) as client:
        client.app.state.session_memory.append(
            Message(text="hi", sender="user", session_id="s1")
        )
        client.app.state.session_memory.append(
            Message(text="hello", sender="user", session_id="s2")
        )

        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload[0]["session_id"] == "s2"
        assert payload[1]["session_id"] == "s1"


def test_get_session_messages_returns_history(tmp_path) -> None:
    with _build_app(tmp_path) as client:
        client.app.state.session_memory.append(
            Message(text="first", sender="user", session_id="s1")
        )
        client.app.state.session_memory.append(
            Message(text="second", sender="assistant", session_id="s1")
        )

        resp = client.get("/api/sessions/s1/messages")
        assert resp.status_code == 200
        payload = resp.json()
        assert [item["text"] for item in payload] == ["first", "second"]
        assert [item["sender"] for item in payload] == ["user", "assistant"]
