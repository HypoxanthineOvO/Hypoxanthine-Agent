from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from hypo_agent.skills.reminder_skill import ReminderSkill


class StubRouter:
    def __init__(self, parsed: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.parsed = parsed or {
            "schedule_type": "once",
            "schedule_value": "2099-03-08T15:00:00+08:00",
            "human_readable": "明天下午3点",
            "timezone": "Asia/Shanghai",
        }

    async def call_lightweight_json(self, prompt: str, *, session_id: str | None = None) -> dict:
        self.calls.append((prompt, {"session_id": session_id}))
        return dict(self.parsed)


class StubStore:
    def __init__(self) -> None:
        self.next_id = 1
        self.rows: dict[int, dict] = {}
        self.created: list[dict] = []
        self.updated: list[tuple[int, dict]] = []
        self.deleted: list[int] = []

    async def create_reminder(self, **kwargs) -> int:
        reminder_id = self.next_id
        self.next_id += 1
        row = {
            "id": reminder_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            **kwargs,
        }
        self.created.append(dict(row))
        self.rows[reminder_id] = row
        return reminder_id

    async def get_reminder(self, reminder_id: int) -> dict | None:
        return self.rows.get(reminder_id)

    async def list_reminders(self, *, status: str | None = "active") -> list[dict]:
        rows = list(self.rows.values())
        if status is None:
            return rows
        return [row for row in rows if row.get("status") == status]

    async def update_reminder(self, reminder_id: int, **kwargs) -> None:
        self.updated.append((reminder_id, dict(kwargs)))
        row = self.rows.get(reminder_id)
        if row is None:
            return
        row.update(kwargs)
        row["updated_at"] = datetime.now(UTC).isoformat()

    async def delete_reminder(self, reminder_id: int) -> None:
        self.deleted.append(reminder_id)
        row = self.rows.get(reminder_id)
        if row is not None:
            row["status"] = "deleted"


class StubScheduler:
    def __init__(self) -> None:
        self.registered: list[dict] = []
        self.removed: list[int] = []
        self._job_ids: set[int] = set()

    async def register_reminder_job(self, reminder: dict) -> None:
        self.registered.append(dict(reminder))
        reminder_id = reminder.get("id")
        if isinstance(reminder_id, int):
            self._job_ids.add(reminder_id)

    async def remove_reminder_job(self, reminder_id: int) -> None:
        self.removed.append(reminder_id)
        self._job_ids.discard(reminder_id)

    def has_job(self, reminder_id: int) -> bool:
        return reminder_id in self._job_ids


def test_reminder_skill_exposes_five_tools() -> None:
    skill = ReminderSkill(
        structured_store=StubStore(),
        scheduler=StubScheduler(),
        model_router=StubRouter(),
    )
    names = [tool["function"]["name"] for tool in skill.tools]
    assert names == [
        "create_reminder",
        "list_reminders",
        "delete_reminder",
        "update_reminder",
        "snooze_reminder",
    ]


def test_create_reminder_preview_uses_lightweight_parser_when_auto_confirm_disabled() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
            auto_confirm=False,
        )
        result = await skill.execute(
            "create_reminder",
            {
                "title": "提醒我开会",
                "description": "项目例会",
                "schedule_type": "once",
                "schedule_value": "明天下午三点",
                "channel": "all",
                "confirm": False,
            },
        )

        assert result.status == "success"
        assert result.result["preview"]["schedule_type"] == "once"
        assert result.result["preview"]["timezone"] == "Asia/Shanghai"
        assert len(router.calls) == 1
        assert store.created == []
        assert scheduler.registered == []

    asyncio.run(_run())


def test_create_reminder_preview_does_not_persist_when_confirm_is_string_false() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
            auto_confirm=False,
        )
        result = await skill.execute(
            "create_reminder",
            {
                "title": "字符串确认值",
                "description": "测试",
                "schedule_type": "once",
                "schedule_value": "明天下午三点",
                "confirm": "false",
            },
        )

        assert result.status == "success"
        assert result.result["requires_confirmation"] is True
        assert store.created == []
        assert scheduler.registered == []

    asyncio.run(_run())


def test_create_reminder_auto_confirm_persists_without_confirm() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
        )
        result = await skill.execute(
            "create_reminder",
            {
                "title": "提醒我开会",
                "description": "项目例会",
                "schedule_type": "once",
                "schedule_value": "2099-03-08T15:00:00+08:00",
                "channel": "all",
            },
        )

        assert result.status == "success"
        assert result.result["reminder_id"] == 1
        assert len(store.created) == 1
        assert store.created[0]["status"] == "active"
        assert len(scheduler.registered) == 1
        assert scheduler.registered[0]["id"] == 1

    asyncio.run(_run())


def test_create_reminder_rejects_past_time(monkeypatch) -> None:
    async def _run() -> None:
        from datetime import datetime, timedelta, UTC
        import hypo_agent.skills.reminder_skill as reminder_module

        fixed = datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed
                return fixed.astimezone(tz)

        monkeypatch.setattr(reminder_module, "datetime", FixedDatetime)

        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=StubRouter(),
        )
        past = (fixed - timedelta(minutes=5)).isoformat()
        result = await skill.execute(
            "create_reminder",
            {
                "title": "过去提醒",
                "schedule_type": "once",
                "schedule_value": past,
            },
        )

        assert result.status == "error"
        assert "in the past" in (result.error_info or "")

    asyncio.run(_run())


def test_create_reminder_accepts_future_time(monkeypatch) -> None:
    async def _run() -> None:
        from datetime import datetime, timedelta, UTC
        import hypo_agent.skills.reminder_skill as reminder_module

        fixed = datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed
                return fixed.astimezone(tz)

        monkeypatch.setattr(reminder_module, "datetime", FixedDatetime)

        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=StubRouter(),
        )
        future = (fixed + timedelta(minutes=5)).isoformat()
        result = await skill.execute(
            "create_reminder",
            {
                "title": "未来提醒",
                "schedule_type": "once",
                "schedule_value": future,
            },
        )

        assert result.status == "success"

    asyncio.run(_run())


def test_create_reminder_confirm_persists_and_registers_scheduler_job_when_required() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
            auto_confirm=False,
        )
        preview = await skill.execute(
            "create_reminder",
            {
                "title": "提醒我开会",
                "description": "项目例会",
                "schedule_type": "once",
                "schedule_value": "2099-03-08T15:00:00+08:00",
                "channel": "all",
            },
        )
        assert preview.status == "success"
        assert store.created == []

        result = await skill.execute(
            "create_reminder",
            {
                "title": "提醒我开会",
                "description": "项目例会",
                "schedule_type": "once",
                "schedule_value": "2099-03-08T15:00:00+08:00",
                "channel": "all",
                "confirm": True,
            },
        )

        assert result.status == "success"
        assert result.result["reminder_id"] == 1
        assert len(store.created) == 1
        assert store.created[0]["status"] == "active"
        assert len(scheduler.registered) == 1
        assert scheduler.registered[0]["id"] == 1

    asyncio.run(_run())


def test_reminder_skill_update_delete_and_snooze() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
        )
        reminder_id = await store.create_reminder(
            title="旧提醒",
            description="旧描述",
            schedule_type="once",
            schedule_value="2099-03-08T15:00:00+08:00",
            channel="all",
            status="active",
            next_run_at="2099-03-08T15:00:00+08:00",
            heartbeat_config=None,
        )

        listed = await skill.execute("list_reminders", {"status": "active"})
        assert listed.status == "success"
        assert len(listed.result["items"]) == 1

        updated = await skill.execute(
            "update_reminder",
            {
                "reminder_id": reminder_id,
                "title": "新提醒",
                "description": "新描述",
                "schedule_type": "cron",
                "schedule_value": "0 * * * *",
            },
        )
        assert updated.status == "success"
        assert store.rows[reminder_id]["title"] == "新提醒"
        assert len(scheduler.registered) >= 1

        before = datetime.now(UTC)
        snoozed = await skill.execute(
            "snooze_reminder",
            {"reminder_id": reminder_id, "duration": "10m"},
        )
        after = datetime.now(UTC)
        assert snoozed.status == "success"
        snooze_value = store.rows[reminder_id]["schedule_value"]
        snooze_at = datetime.fromisoformat(snooze_value)
        assert before + timedelta(minutes=9) <= snooze_at <= after + timedelta(minutes=11)
        assert reminder_id in scheduler.removed

        deleted = await skill.execute("delete_reminder", {"reminder_id": reminder_id})
        assert deleted.status == "success"
        assert reminder_id in store.deleted
        assert reminder_id in scheduler.removed

    asyncio.run(_run())


def test_list_reminders_defaults_to_all_non_deleted() -> None:
    async def _run() -> None:
        skill = ReminderSkill(
            structured_store=StubStore(),
            scheduler=StubScheduler(),
            model_router=StubRouter(),
        )
        await skill.structured_store.create_reminder(
            title="A",
            description="",
            schedule_type="once",
            schedule_value="2099-03-08T15:00:00+08:00",
            channel="all",
            status="active",
            next_run_at="2099-03-08T15:00:00+08:00",
            heartbeat_config=None,
        )
        await skill.structured_store.create_reminder(
            title="B",
            description="",
            schedule_type="once",
            schedule_value="2099-03-08T16:00:00+08:00",
            channel="all",
            status="completed",
            next_run_at=None,
            heartbeat_config=None,
        )

        listed = await skill.execute("list_reminders", {})
        assert listed.status == "success"
        assert len(listed.result["items"]) == 2

    asyncio.run(_run())


def test_update_reminder_paused_removes_scheduler_job() -> None:
    async def _run() -> None:
        router = StubRouter()
        store = StubStore()
        scheduler = StubScheduler()
        skill = ReminderSkill(
            structured_store=store,
            scheduler=scheduler,
            model_router=router,
        )

        created = await skill.execute(
            "create_reminder",
            {
                "title": "提醒我开会",
                "description": "项目例会",
                "schedule_type": "once",
                "schedule_value": "2099-03-08T15:00:00+08:00",
                "channel": "all",
            },
        )
        reminder_id = int(created.result["reminder_id"])
        assert scheduler.has_job(reminder_id) is True

        paused = await skill.execute(
            "update_reminder",
            {"reminder_id": reminder_id, "status": "paused"},
        )
        assert paused.status == "success"
        assert scheduler.has_job(reminder_id) is False

    asyncio.run(_run())


def test_create_reminder_tool_schedule_value_description_mentions_iso8601() -> None:
    skill = ReminderSkill(
        structured_store=StubStore(),
        scheduler=StubScheduler(),
        model_router=StubRouter(),
    )
    create_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "create_reminder")
    description = create_tool["function"]["parameters"]["properties"]["schedule_value"]["description"]
    assert "ISO 8601" in description


def test_create_reminder_tool_omits_confirm_when_auto_confirm_enabled() -> None:
    skill = ReminderSkill(
        structured_store=StubStore(),
        scheduler=StubScheduler(),
        model_router=StubRouter(),
        auto_confirm=True,
    )
    create_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "create_reminder")
    properties = create_tool["function"]["parameters"]["properties"]
    assert "confirm" not in properties


def test_create_reminder_tool_includes_confirm_when_auto_confirm_disabled() -> None:
    skill = ReminderSkill(
        structured_store=StubStore(),
        scheduler=StubScheduler(),
        model_router=StubRouter(),
        auto_confirm=False,
    )
    create_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "create_reminder")
    properties = create_tool["function"]["parameters"]["properties"]
    assert "confirm" in properties


def test_reminder_tools_define_heartbeat_config_array_items() -> None:
    skill = ReminderSkill(
        structured_store=StubStore(),
        scheduler=StubScheduler(),
        model_router=StubRouter(),
    )

    create_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "create_reminder")
    create_heartbeat = create_tool["function"]["parameters"]["properties"]["heartbeat_config"]
    assert create_heartbeat["type"] == "array"
    assert create_heartbeat["items"]["type"] == "object"
    assert create_heartbeat["items"]["properties"]["check_type"]["enum"] == [
        "file_exists",
        "process_running",
        "http_status",
        "custom_command",
    ]

    update_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "update_reminder")
    update_heartbeat = update_tool["function"]["parameters"]["properties"]["heartbeat_config"]
    assert update_heartbeat["type"] == "array"
    assert update_heartbeat["items"]["required"] == ["check_type", "target"]
