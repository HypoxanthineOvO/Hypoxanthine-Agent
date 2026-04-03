from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import AppDeps
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message
from tests.shared import NoopRouter, NoopScheduler, PassivePipeline


def test_ws_receives_proactive_push_message(app_factory) -> None:
    app = app_factory(pipeline=PassivePipeline())
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

def test_ws_receives_scheduler_event_through_pipeline_consumer(tmp_path) -> None:
    queue = EventQueue()
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        event_queue=queue,
    )
    from hypo_agent.gateway.app import create_app

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
