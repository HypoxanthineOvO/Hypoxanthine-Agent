from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class PassivePipeline:
    async def start_event_consumer(self) -> None:
        return None

    async def stop_event_consumer(self) -> None:
        return None

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def test_ws_receives_proactive_push_message() -> None:
    app = create_app(auth_token="test-token", pipeline=PassivePipeline())
    with TestClient(app) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            payload = Message(
                text="🔔 提醒：喝水",
                sender="assistant",
                session_id="main",
                message_tag="reminder",
            ).model_dump(mode="json")
            asyncio.run(app.state.push_ws_message(payload))
            event = ws.receive_json()
            assert event["text"] == "🔔 提醒：喝水"
            assert event["session_id"] == "main"
            assert event["message_tag"] == "reminder"


class NoopScheduler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class NoopRouter:
    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, messages, tools, session_id
        return {"text": "ok", "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield "ok"


def test_ws_receives_scheduler_event_through_pipeline_consumer(tmp_path) -> None:
    queue = EventQueue()
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        event_queue=queue,
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=AppDeps(
            session_memory=session_memory,
            structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
            event_queue=queue,
            scheduler=NoopScheduler(),
        ),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            asyncio.run(
                queue.put(
                    {
                        "event_type": "reminder_trigger",
                        "session_id": "main",
                        "title": "喝水",
                        "description": "十分钟后",
                    }
                )
            )
            event = ws.receive_json()
            assert event["session_id"] == "main"
            assert event["message_tag"] == "reminder"
            assert "喝水" in event["text"]
