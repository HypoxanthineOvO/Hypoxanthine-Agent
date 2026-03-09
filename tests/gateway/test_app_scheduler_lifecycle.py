from __future__ import annotations

from fastapi.testclient import TestClient

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
