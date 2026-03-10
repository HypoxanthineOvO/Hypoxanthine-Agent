from __future__ import annotations

import asyncio

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.heartbeat import HeartbeatService


class StubStore:
    def __init__(self, overdue_rows: list[dict] | None = None) -> None:
        self.overdue_rows = overdue_rows or []

    async def list_overdue_pending_reminders(self, *, limit: int = 20) -> list[dict]:
        del limit
        return list(self.overdue_rows)


class StubScheduler:
    def __init__(self, running: bool = True) -> None:
        self.is_running = running


class StubRouter:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def call_lightweight_json(self, prompt: str, *, session_id: str | None = None) -> dict:
        del session_id
        self.prompts.append(prompt)
        return dict(self.payload)


def test_heartbeat_silent_when_no_events() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(overdue_rows=[])
        router = StubRouter({"should_push": False, "summary": "一切正常"})
        scheduler = StubScheduler(running=True)
        service = HeartbeatService(
            structured_store=store,
            model_router=router,
            message_queue=queue,
            scheduler=scheduler,
            default_session_id="main",
        )

        result = await service.run()
        assert result["should_push"] is False
        assert queue.empty() is True

    asyncio.run(_run())


def test_heartbeat_pushes_when_should_push_true() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(overdue_rows=[{"id": 1, "title": "过期提醒"}])
        router = StubRouter({"should_push": True, "summary": "检测到 1 条漏触发提醒"})
        scheduler = StubScheduler(running=True)
        service = HeartbeatService(
            structured_store=store,
            model_router=router,
            message_queue=queue,
            scheduler=scheduler,
            default_session_id="main",
        )

        result = await service.run()
        event = await queue.get()
        queue.task_done()

        assert result["should_push"] is True
        assert event["event_type"] == "heartbeat_trigger"
        assert event["message_tag"] == "heartbeat"
        assert event["summary"] == "检测到 1 条漏触发提醒"

    asyncio.run(_run())


def test_heartbeat_register_event_source_invokes_callbacks() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(overdue_rows=[])
        router = StubRouter({"should_push": False, "summary": "quiet"})
        scheduler = StubScheduler(running=True)
        service = HeartbeatService(
            structured_store=store,
            model_router=router,
            message_queue=queue,
            scheduler=scheduler,
            default_session_id="main",
        )
        called: list[str] = []

        async def async_source() -> dict:
            called.append("async_source")
            return {"name": "async_source", "new_items": 0}

        def sync_source() -> dict:
            called.append("sync_source")
            return {"name": "sync_source", "new_items": 0}

        service.register_event_source("async_source", async_source)
        service.register_event_source("sync_source", sync_source)

        await service.run()
        assert set(called) == {"async_source", "sync_source"}

    asyncio.run(_run())
