from __future__ import annotations

import asyncio

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.scheduler import SchedulerService


class StubStore:
    def __init__(self, reminders: list[dict] | None = None) -> None:
        self.reminders = reminders or []
        self.list_calls: list[str | None] = []
        self.completed: list[int] = []

    async def list_reminders(self, *, status: str | None = "active") -> list[dict]:
        self.list_calls.append(status)
        if status is None:
            return list(self.reminders)
        return [item for item in self.reminders if item.get("status") == status]

    async def get_reminder(self, reminder_id: int) -> dict | None:
        for item in self.reminders:
            if int(item.get("id", 0)) == int(reminder_id):
                return item
        return None

    async def mark_reminder_completed(self, reminder_id: int) -> None:
        self.completed.append(reminder_id)
        for item in self.reminders:
            if int(item.get("id", 0)) == int(reminder_id):
                item["status"] = "completed"

    async def set_reminder_next_run_at(self, reminder_id: int, next_run_at: str | None) -> None:
        for item in self.reminders:
            if int(item.get("id", 0)) == int(reminder_id):
                item["next_run_at"] = next_run_at


def test_scheduler_start_stop_lifecycle() -> None:
    async def _run() -> None:
        service = SchedulerService(
            structured_store=StubStore(),
            event_queue=EventQueue(),
        )
        assert service.is_running is False
        await service.start()
        assert service.is_running is True
        await service.stop()
        assert service.is_running is False

    asyncio.run(_run())


def test_scheduler_start_rebuilds_active_reminders() -> None:
    async def _run() -> None:
        reminders = [
            {
                "id": 1,
                "title": "一次性提醒",
                "description": "",
                "schedule_type": "once",
                "schedule_value": "2099-01-01T00:00:00+00:00",
                "status": "active",
            },
            {
                "id": 2,
                "title": "周期提醒",
                "description": "",
                "schedule_type": "cron",
                "schedule_value": "*/5 * * * *",
                "status": "active",
            },
        ]
        store = StubStore(reminders)
        service = SchedulerService(
            structured_store=store,
            event_queue=EventQueue(),
        )
        await service.start()

        assert store.list_calls == ["active"]
        assert service.has_job(1) is True
        assert service.has_job(2) is True

        await service.stop()

    asyncio.run(_run())


def test_scheduler_parses_cron_timezone_prefix() -> None:
    expression, timezone = SchedulerService.parse_cron_schedule(
        "CRON_TZ=Asia/Shanghai 0 15 * * *",
        default_timezone="UTC",
    )
    assert expression == "0 15 * * *"
    assert timezone == "Asia/Shanghai"


def test_scheduler_enqueues_reminder_event_on_trigger() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 10,
                    "title": "一次性提醒",
                    "description": "测试触发",
                    "schedule_type": "once",
                    "schedule_value": "2099-01-01T00:00:00+00:00",
                    "status": "active",
                    "channel": "all",
                }
            ]
        )
        service = SchedulerService(structured_store=store, event_queue=queue)
        await service._handle_job_trigger(reminder_id=10)

        event = await queue.get()
        queue.task_done()
        assert event["event_type"] == "reminder_trigger"
        assert event["reminder_id"] == 10
        assert event["session_id"] == "main"
        assert store.completed == [10]
        assert store.reminders[0]["status"] == "completed"

    asyncio.run(_run())


class StubDecisionRouter:
    def __init__(self, decision: str) -> None:
        self.decision = decision

    async def call_lightweight_json(self, prompt: str, *, session_id: str | None = None) -> dict:
        del prompt, session_id
        return {"decision": self.decision, "reason": "stub"}


def test_heartbeat_normal_is_silent() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 20,
                    "title": "巡检",
                    "description": "服务健康检查",
                    "schedule_type": "cron",
                    "schedule_value": "*/5 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [{"check_type": "file_exists", "target": "/tmp/ok"}],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
            model_router=StubDecisionRouter("normal"),
        )
        await service._handle_job_trigger(reminder_id=20)
        assert queue.empty() is True

    asyncio.run(_run())


def test_heartbeat_abnormal_enqueues_event() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 21,
                    "title": "巡检",
                    "description": "服务健康检查",
                    "schedule_type": "cron",
                    "schedule_value": "*/5 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [{"check_type": "file_exists", "target": "/tmp/ok"}],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
            model_router=StubDecisionRouter("abnormal"),
        )
        await service._handle_job_trigger(reminder_id=21)

        event = await queue.get()
        queue.task_done()
        assert event["event_type"] == "heartbeat_trigger"
        assert event["reminder_id"] == 21
        assert event["title"] == "巡检"

    asyncio.run(_run())
