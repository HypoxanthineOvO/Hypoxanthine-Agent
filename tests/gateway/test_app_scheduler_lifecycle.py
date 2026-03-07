from __future__ import annotations

from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class RecordingScheduler:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class RecordingPipeline:
    def __init__(self) -> None:
        self.consumer_started = 0
        self.consumer_stopped = 0

    async def start_event_consumer(self) -> None:
        self.consumer_started += 1

    async def stop_event_consumer(self) -> None:
        self.consumer_stopped += 1

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class OrderedPipeline:
    def __init__(self, scheduler: RecordingScheduler) -> None:
        self.scheduler = scheduler
        self.consumer_started = 0
        self.consumer_stopped = 0
        self.on_proactive_message = None

    async def start_event_consumer(self) -> None:
        self.consumer_started += 1
        assert self.scheduler.started == 1
        assert self.on_proactive_message is not None

    async def stop_event_consumer(self) -> None:
        self.consumer_stopped += 1

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class DummyEventQueue:
    async def put(self, event):  # pragma: no cover - interface placeholder
        del event

    async def get(self):  # pragma: no cover - interface placeholder
        return {}

    def task_done(self):  # pragma: no cover - interface placeholder
        return None

    def empty(self):  # pragma: no cover - interface placeholder
        return True

    def qsize(self):  # pragma: no cover - interface placeholder
        return 0


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


def test_app_lifespan_starts_and_stops_scheduler_and_pipeline_consumer(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    with TestClient(app):
        assert scheduler.started == 1
        assert pipeline.consumer_started == 1

    assert scheduler.stopped == 1
    assert pipeline.consumer_stopped == 1


def test_app_lifespan_starts_consumer_after_scheduler_and_callback_wiring(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = OrderedPipeline(scheduler)
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    with TestClient(app):
        assert scheduler.started == 1
        assert pipeline.consumer_started == 1

    assert pipeline.consumer_stopped == 1
    assert scheduler.stopped == 1


def test_event_consumer_starts_on_lifespan(tmp_path) -> None:
    queue = EventQueue()
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        event_queue=queue,
    )
    deps = AppDeps(
        session_memory=session_memory,
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=RecordingScheduler(),
        event_queue=queue,
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)

    with TestClient(app):
        task = app.state.pipeline._event_consumer_task
        assert task is not None
        assert task.done() is False
