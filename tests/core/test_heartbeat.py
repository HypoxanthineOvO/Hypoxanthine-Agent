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
        self._has_heartbeat_job = running
        self._active_jobs = 1 if running else 0

    def has_job_id(self, job_id: str) -> bool:
        return job_id == "heartbeat" and self._has_heartbeat_job

    def get_active_job_count(self) -> int:
        return self._active_jobs


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


def test_heartbeat_uses_custom_prompt_template_with_runtime_values() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(overdue_rows=[{"id": 1, "title": "过期提醒"}])
        router = StubRouter({"should_push": False, "summary": "一切正常"})
        scheduler = StubScheduler(running=True)
        service = HeartbeatService(
            structured_store=store,
            model_router=router,
            message_queue=queue,
            scheduler=scheduler,
            default_session_id="main",
            decision_prompt_template=(
                "你是自定义心跳判定器。\n"
                "checks=${checks}\n"
                "overdue=${overdue}\n"
                "sources=${sources}"
            ),
        )

        await service.run()

        assert len(router.prompts) == 1
        assert "你是自定义心跳判定器。" in router.prompts[0]
        assert "${checks}" not in router.prompts[0]
        assert '"db_ok": true' in router.prompts[0]
        assert '"title": "过期提醒"' in router.prompts[0]

    asyncio.run(_run())


def test_heartbeat_status_reports_running_when_scheduler_has_active_jobs() -> None:
    service = HeartbeatService(
        structured_store=StubStore(overdue_rows=[]),
        model_router=None,
        message_queue=EventQueue(),
        scheduler=StubScheduler(running=True),
        default_session_id="main",
    )
    service.last_heartbeat_at = "2026-03-13T12:00:00+00:00"

    class SchedulerWithoutDedicatedHeartbeatJob:
        is_running = True

        def has_job_id(self, job_id: str) -> bool:
            del job_id
            return False

        def get_active_job_count(self) -> int:
            return 2

    status = service.get_status(scheduler=SchedulerWithoutDedicatedHeartbeatJob())

    assert status["status"] == "running"
    assert status["active_tasks"] == 2
