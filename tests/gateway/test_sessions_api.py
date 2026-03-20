from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Attachment, Message


class DummyPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}


def _build_app(tmp_path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        permission_manager=object(),
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
            Message(
                text="first",
                sender="user",
                session_id="s1",
                attachments=[
                    Attachment(
                        type="image",
                        url="/tmp/demo.png",
                        filename="demo.png",
                        mime_type="image/png",
                        size_bytes=10,
                    )
                ],
            )
        )
        client.app.state.session_memory.append(
            Message(text="second", sender="assistant", session_id="s1")
        )

        resp = client.get("/api/sessions/s1/messages")
        assert resp.status_code == 200
        payload = resp.json()
        assert [item["text"] for item in payload] == ["first", "second"]
        assert [item["sender"] for item in payload] == ["user", "assistant"]
        assert payload[0]["attachments"][0]["filename"] == "demo.png"


def test_get_session_tool_invocations_requires_token(tmp_path) -> None:
    with _build_app(tmp_path) as client:
        response = client.get("/api/sessions/s1/tool-invocations")
        assert response.status_code == 401


def test_get_session_tool_invocations_returns_rows(tmp_path) -> None:
    with _build_app(tmp_path) as client:
        asyncio.run(
            client.app.state.structured_store.record_tool_invocation(
                session_id="s1",
                tool_name="run_command",
                skill_name="tmux",
                params_json='{"command":"echo hi"}',
                status="success",
                result_summary="ok",
                duration_ms=11.2,
                error_info="",
                compressed_meta_json='{"cache_id":"abc","original_chars":1000,"compressed_chars":120}',
            )
        )

        response = client.get(
            "/api/sessions/s1/tool-invocations",
            params={"token": "test-token"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        row = payload[0]
        assert row["session_id"] == "s1"
        assert row["tool_name"] == "run_command"
        assert row["skill_name"] == "tmux"
        assert row["params_json"] == '{"command":"echo hi"}'
        assert row["status"] == "success"
        assert row["result_summary"] == "ok"


def test_delete_session_also_clears_structured_store_rows(tmp_path) -> None:
    with _build_app(tmp_path) as client:
        client.app.state.session_memory.append(
            Message(text="hello", sender="user", session_id="s1")
        )
        asyncio.run(
            client.app.state.structured_store.record_token_usage(
                session_id="s1",
                requested_model="Gemini3Pro",
                resolved_model="Gemini3Pro",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=99.0,
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

        response = client.delete(
            "/api/sessions/s1",
            params={"token": "test-token"},
        )
        token_rows = asyncio.run(client.app.state.structured_store.list_token_usage("s1"))
        invocation_rows = asyncio.run(
            client.app.state.structured_store.list_tool_invocations(session_id="s1")
        )
        session_rows = asyncio.run(client.app.state.structured_store.list_sessions())

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert token_rows == []
    assert invocation_rows == []
    assert all(row["session_id"] != "s1" for row in session_rows)
