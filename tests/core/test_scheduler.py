from __future__ import annotations

import asyncio

import pytest

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.scheduler import SchedulerService


class StubStore:
    def __init__(self, reminders: list[dict] | None = None) -> None:
        self.reminders = reminders or []
        self.list_calls: list[str | None] = []
        self.completed: list[int] = []
        self.updated: list[tuple[int, dict]] = []

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

    async def update_reminder(self, reminder_id: int, **kwargs) -> None:
        self.updated.append((reminder_id, dict(kwargs)))
        for item in self.reminders:
            if int(item.get("id", 0)) == int(reminder_id):
                item.update(kwargs)

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

        assert store.list_calls == ["active", "active"]
        assert service.has_job(1) is True
        assert service.has_job(2) is True

        await service.stop()

    asyncio.run(_run())


def test_scheduler_start_skips_invalid_reminder_without_crashing() -> None:
    async def _run() -> None:
        reminders = [
            {
                "id": 99,
                "title": "坏提醒",
                "description": "",
                "schedule_type": "once",
                "schedule_value": "+1 minute",
                "status": "active",
            }
        ]
        service = SchedulerService(
            structured_store=StubStore(reminders),
            event_queue=EventQueue(),
        )
        await service.start()
        assert service.is_running is True
        assert service.has_job(99) is False
        await service.stop()

    asyncio.run(_run())


def test_scheduler_parses_cron_timezone_prefix() -> None:
    expression, timezone = SchedulerService.parse_cron_schedule(
        "CRON_TZ=Asia/Shanghai 0 15 * * *",
        default_timezone="UTC",
    )
    assert expression == "0 15 * * *"
    assert timezone == "Asia/Shanghai"


def test_scheduler_uses_default_timezone_for_naive_once_datetime() -> None:
    async def _run() -> None:
        store = StubStore(
            [
                {
                    "id": 30,
                    "title": "本地时区提醒",
                    "description": "",
                    "schedule_type": "once",
                    "schedule_value": "2099-01-01T08:00:00",
                    "status": "active",
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=EventQueue(),
            default_timezone="Asia/Shanghai",
        )
        await service.start()
        job = service._scheduler.get_job("reminder:30")
        assert job is not None
        assert job.next_run_time is not None
        assert str(job.next_run_time.tzinfo) == "Asia/Shanghai"
        await service.stop()

    asyncio.run(_run())


def test_scheduler_once_trigger_does_not_probe_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    import apscheduler.triggers.date as date_trigger_module

    service = SchedulerService(
        structured_store=StubStore(),
        event_queue=EventQueue(),
        default_timezone="Asia/Shanghai",
    )
    reminder = {
        "id": 31,
        "title": "显式时区提醒",
        "description": "",
        "schedule_type": "once",
        "schedule_value": "2099-01-01T08:00:00+08:00",
        "status": "active",
    }

    def _unexpected_localzone():
        raise AssertionError("get_localzone should not be called for once reminders")

    monkeypatch.setattr(date_trigger_module, "get_localzone", _unexpected_localzone)

    trigger = service._build_trigger(reminder)
    assert str(trigger.run_date.tzinfo) == "Asia/Shanghai"


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
                    "session_id": "smoke-123",
                }
            ]
        )
        service = SchedulerService(structured_store=store, event_queue=queue)
        await service._handle_job_trigger(reminder_id=10)

        event = await queue.get()
        queue.task_done()
        assert event["event_type"] == "reminder_trigger"
        assert event["reminder_id"] == 10
        assert event["session_id"] == "smoke-123"
        assert store.completed == [10]
        assert store.reminders[0]["status"] == "completed"

    asyncio.run(_run())


def test_scheduler_marks_past_once_reminders_as_missed_on_start() -> None:
    async def _run() -> None:
        store = StubStore(
            [
                {
                    "id": 11,
                    "title": "过期提醒",
                    "description": "",
                    "schedule_type": "once",
                    "schedule_value": "2000-01-01T00:00:00+08:00",
                    "status": "active",
                    "channel": "all",
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=EventQueue(),
            default_timezone="Asia/Shanghai",
        )
        await service.start()

        row = await store.get_reminder(11)
        assert row is not None
        assert row["status"] == "missed"
        assert service.has_job(11) is False

        await service.stop()

    asyncio.run(_run())


def test_scheduler_job_missed_marks_once_reminder_missed() -> None:
    async def _run() -> None:
        store = StubStore(
            [
                {
                    "id": 12,
                    "title": "错过提醒",
                    "description": "",
                    "schedule_type": "once",
                    "schedule_value": "2099-01-01T00:00:00+08:00",
                    "status": "active",
                    "channel": "all",
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=EventQueue(),
            misfire_grace_time_seconds=1,
        )
        await service.start()
        service._on_job_missed(type("Event", (), {"job_id": "reminder:12"})())
        await asyncio.sleep(0.05)

        row = await store.get_reminder(12)
        assert row is not None
        assert row["status"] == "missed"
        assert service.has_job(12) is False

        await service.stop()

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


def test_legacy_email_heartbeat_enqueues_email_scan_event_for_important_mail() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 31,
                    "title": "📧 定时邮件推送（Heartbeat）",
                    "description": "每 30 分钟扫描一次邮箱",
                    "schedule_type": "cron",
                    "schedule_value": "*/30 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [
                        {
                            "check_interval": 1800,
                            "timeout": 300,
                            "alert_threshold": 1,
                            "action": "push_email_summary",
                        }
                    ],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
        )

        async def scan_emails() -> dict:
            return {
                "accounts_scanned": 1,
                "accounts_failed": 0,
                "new_emails": 2,
                "items": [
                    {"category": "important", "subject": "重要通知"},
                    {"category": "archive", "subject": "归档通知"},
                ],
                "summary": "📧 邮件扫描完成：🔴 1 封重要；⚪ 0 封普通；📂 1 封归档",
            }

        service.set_email_scan_executor(scan_emails)
        await service._handle_job_trigger(reminder_id=31)

        event = await queue.get()
        queue.task_done()
        assert event["event_type"] == "email_scan_trigger"
        assert "1 封重要" in event["summary"]

    asyncio.run(_run())


def test_legacy_email_heartbeat_skips_when_email_skill_not_enabled() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 32,
                    "title": "📧 定时邮件推送（Heartbeat）",
                    "description": "每 30 分钟扫描一次邮箱",
                    "schedule_type": "cron",
                    "schedule_value": "*/30 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [
                        {
                            "check_interval": 1800,
                            "timeout": 300,
                            "alert_threshold": 1,
                            "action": "push_email_summary",
                        }
                    ],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
        )

        await service._handle_job_trigger(reminder_id=32)
        assert queue.empty() is True

    asyncio.run(_run())


def test_legacy_email_heartbeat_gracefully_handles_connection_failure() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 33,
                    "title": "📧 定时邮件推送（Heartbeat）",
                    "description": "每 30 分钟扫描一次邮箱",
                    "schedule_type": "cron",
                    "schedule_value": "*/30 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [
                        {
                            "check_interval": 1800,
                            "timeout": 300,
                            "alert_threshold": 1,
                            "action": "push_email_summary",
                        }
                    ],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
        )

        async def scan_emails() -> dict:
            return {
                "accounts_scanned": 0,
                "accounts_failed": 1,
                "new_emails": 0,
                "items": [{"status": "failed", "error": "imap auth failed"}],
                "summary": "📧 邮件扫描完成：🔴 0 封重要；⚪ 0 封普通；📂 0 封归档",
            }

        service.set_email_scan_executor(scan_emails)
        await service._handle_job_trigger(reminder_id=33)
        assert queue.empty() is True

    asyncio.run(_run())


def test_legacy_email_heartbeat_skips_push_when_no_important_mail() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 34,
                    "title": "📧 定时邮件推送（Heartbeat）",
                    "description": "每 30 分钟扫描一次邮箱",
                    "schedule_type": "cron",
                    "schedule_value": "*/30 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [
                        {
                            "check_interval": 1800,
                            "timeout": 300,
                            "alert_threshold": 1,
                            "action": "push_email_summary",
                        }
                    ],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
        )

        async def scan_emails() -> dict:
            return {
                "accounts_scanned": 1,
                "accounts_failed": 0,
                "new_emails": 2,
                "items": [
                    {"category": "archive", "subject": "归档通知"},
                    {"category": "low_priority", "subject": "普通通知"},
                ],
                "summary": "📧 邮件扫描完成：🔴 0 封重要；⚪ 1 封普通；📂 1 封归档",
            }

        service.set_email_scan_executor(scan_emails)
        await service._handle_job_trigger(reminder_id=34)
        assert queue.empty() is True

    asyncio.run(_run())


def test_legacy_email_heartbeat_passes_heartbeat_trigger_context_when_supported() -> None:
    async def _run() -> None:
        queue = EventQueue()
        store = StubStore(
            [
                {
                    "id": 35,
                    "title": "📧 定时邮件推送（Heartbeat）",
                    "description": "每 30 分钟扫描一次邮箱",
                    "schedule_type": "cron",
                    "schedule_value": "*/30 * * * *",
                    "status": "active",
                    "channel": "all",
                    "heartbeat_config": [
                        {
                            "check_interval": 1800,
                            "timeout": 300,
                            "alert_threshold": 1,
                            "action": "push_email_summary",
                        }
                    ],
                }
            ]
        )
        service = SchedulerService(
            structured_store=store,
            event_queue=queue,
        )
        seen_params: list[dict] = []

        async def scan_emails(*, params=None) -> dict:
            seen_params.append(dict(params or {}))
            return {
                "accounts_scanned": 1,
                "accounts_failed": 0,
                "new_emails": 1,
                "items": [
                    {"category": "important", "subject": "重要通知"},
                ],
                "summary": "📧 邮件扫描完成：🔴 1 封重要；⚪ 0 封普通；📂 0 封归档",
            }

        service.set_email_scan_executor(scan_emails)
        await service._handle_job_trigger(reminder_id=35)

        assert seen_params == [{"triggered_by": "heartbeat"}]

    asyncio.run(_run())


def test_scheduler_registers_interval_job_for_heartbeat() -> None:
    async def _run() -> None:
        service = SchedulerService(
            structured_store=StubStore(),
            event_queue=EventQueue(),
        )
        await service.start()

        async def heartbeat_job() -> None:
            return None

        service.register_interval_job("heartbeat", 1, heartbeat_job)
        job = service._scheduler.get_job("heartbeat")
        assert job is not None
        assert job.trigger is not None

        await service.stop()

    asyncio.run(_run())


def test_scheduler_registers_cron_job_for_heartbeat() -> None:
    async def _run() -> None:
        service = SchedulerService(
            structured_store=StubStore(),
            event_queue=EventQueue(),
            default_timezone="Asia/Shanghai",
        )

        async def heartbeat_job() -> None:
            return None

        service.register_cron_job("heartbeat", "*/10 * * * *", heartbeat_job)
        job = service._scheduler.get_job("heartbeat")

        assert job is not None
        assert job.trigger is not None
        assert "minute='*/10'" in str(job.trigger)

    asyncio.run(_run())


def test_scheduler_registers_interval_job_for_email_scan() -> None:
    async def _run() -> None:
        service = SchedulerService(
            structured_store=StubStore(),
            event_queue=EventQueue(),
        )
        await service.start()

        async def email_scan_job() -> None:
            return None

        service.register_interval_job("email_scan", 5, email_scan_job)
        job = service._scheduler.get_job("email_scan")
        assert job is not None
        assert job.trigger is not None

        await service.stop()

    asyncio.run(_run())


def test_scheduler_registers_cron_job_for_memory_gc() -> None:
    async def _run() -> None:
        service = SchedulerService(
            structured_store=StubStore(),
            event_queue=EventQueue(),
            default_timezone="Asia/Shanghai",
        )

        async def gc_job() -> None:
            return None

        service.register_cron_job("memory_gc", "0 4 * * *", gc_job)
        job = service._scheduler.get_job("memory_gc")

        assert job is not None
        assert job.trigger is not None
        assert "hour='4'" in str(job.trigger)
        assert "minute='0'" in str(job.trigger)

    asyncio.run(_run())
