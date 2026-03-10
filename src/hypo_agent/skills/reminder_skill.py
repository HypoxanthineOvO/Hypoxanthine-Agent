from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class ReminderSkill(BaseSkill):
    name = "reminder"
    description = "Create, list, update, delete, and snooze reminders."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        structured_store: Any,
        scheduler: Any,
        model_router: Any | None = None,
        auto_confirm: bool = True,
    ) -> None:
        self.structured_store = structured_store
        self.scheduler = scheduler
        self.model_router = model_router
        self.auto_confirm = bool(auto_confirm)

    @property
    def tools(self) -> list[dict[str, Any]]:
        create_params: dict[str, Any] = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "schedule_type": {"type": "string", "enum": ["once", "cron"]},
                "schedule_value": {
                    "type": "string",
                    "description": (
                        'For schedule_type="once": an ISO 8601 datetime string in '
                        'the user local timezone (e.g. "2026-03-07T19:51:00+08:00"). '
                        "MUST be in the future. Refer to the current server time in "
                        "[System Context] for accuracy. "
                        'Example: "2026-03-10T08:30:00+08:00". '
                        'Do NOT use relative expressions like "+1 minute" or "+30m" - '
                        "calculate an absolute datetime first. "
                        'For schedule_type="cron": a standard cron expression, '
                        'optionally prefixed with CRON_TZ=<timezone> '
                        '(e.g. "CRON_TZ=Asia/Shanghai 30 9 * * *").'
                    ),
                },
                "channel": {"type": "string", "default": "all"},
                "heartbeat_config": {"type": "array"},
            },
            "required": ["title", "schedule_type", "schedule_value"],
        }
        create_description = "Create and schedule a reminder immediately."
        if not self.auto_confirm:
            create_params["properties"]["confirm"] = {"type": "boolean", "default": False}
            create_description = (
                "Create a reminder. confirm=false returns parsed schedule preview; "
                "confirm=true persists and schedules it."
            )

        return [
            {
                "type": "function",
                "function": {
                    "name": "create_reminder",
                    "description": create_description,
                    "parameters": create_params,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_reminders",
                    "description": (
                        "List reminders. Default returns all non-deleted reminders "
                        "(active/completed/missed/paused)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "description": (
                                    "Optional status filter: active, paused, completed, missed, "
                                    "or deleted. Use all/omit to list all non-deleted reminders."
                                ),
                                "default": "all",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_reminder",
                    "description": "Soft delete a reminder and remove its scheduled job.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reminder_id": {"type": "integer"},
                        },
                        "required": ["reminder_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "update_reminder",
                    "description": "Update reminder fields and reschedule.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reminder_id": {"type": "integer"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "schedule_type": {"type": "string", "enum": ["once", "cron"]},
                            "schedule_value": {"type": "string"},
                            "channel": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["active", "paused", "completed", "missed", "deleted"],
                            },
                            "next_run_at": {"type": "string"},
                            "heartbeat_config": {"type": "array"},
                        },
                        "required": ["reminder_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "snooze_reminder",
                    "description": "Snooze an active reminder by duration, e.g. 10m/2h/1d.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reminder_id": {"type": "integer"},
                            "duration": {"type": "string"},
                        },
                        "required": ["reminder_id", "duration"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "create_reminder":
            return await self._create_reminder(params)
        if tool_name == "list_reminders":
            return await self._list_reminders(params)
        if tool_name == "delete_reminder":
            return await self._delete_reminder(params)
        if tool_name == "update_reminder":
            return await self._update_reminder(params)
        if tool_name == "snooze_reminder":
            return await self._snooze_reminder(params)
        return SkillOutput(
            status="error",
            error_info=f"Unsupported tool '{tool_name}' for reminder skill",
        )

    async def _create_reminder(self, params: dict[str, Any]) -> SkillOutput:
        title = str(params.get("title") or "").strip()
        if not title:
            return SkillOutput(status="error", error_info="title is required")

        schedule_type = str(params.get("schedule_type") or "").strip().lower()
        schedule_value = str(params.get("schedule_value") or "").strip()
        if schedule_type not in {"once", "cron"}:
            return SkillOutput(status="error", error_info="schedule_type must be 'once' or 'cron'")
        if not schedule_value:
            return SkillOutput(status="error", error_info="schedule_value is required")

        description_raw = params.get("description")
        description = str(description_raw).strip() if description_raw is not None else None
        channel = str(params.get("channel") or "all")
        heartbeat_config = self._json_dumps_or_none(params.get("heartbeat_config"))
        confirm = self._parse_confirm(params.get("confirm", False))

        if not self.auto_confirm and not confirm:
            preview = await self._parse_schedule_preview(
                schedule_type=schedule_type,
                schedule_value=schedule_value,
            )
            return SkillOutput(
                status="success",
                result={
                    "requires_confirmation": True,
                    "title": title,
                    "description": description,
                    "preview": preview,
                },
            )

        if schedule_type == "once":
            try:
                parsed = datetime.fromisoformat(schedule_value)
            except ValueError:
                return SkillOutput(
                    status="error",
                    error_info=(
                        'For schedule_type="once", schedule_value must be an absolute '
                        "ISO 8601 datetime."
                    ),
                )
            parsed = self._ensure_timezone(parsed)
            now = datetime.now(parsed.tzinfo or self._default_timezone())
            if parsed < now - timedelta(seconds=30):
                return SkillOutput(
                    status="error",
                    error_info=(
                        f"trigger_time '{schedule_value}' is in the past. "
                        f"Current server time: {now.isoformat()}. "
                        "Please provide a future time."
                    ),
                )

        reminder_id = await self.structured_store.create_reminder(
            title=title,
            description=description,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            channel=channel,
            status="active",
            next_run_at=schedule_value if schedule_type == "once" else None,
            heartbeat_config=heartbeat_config,
        )
        reminder = await self.structured_store.get_reminder(reminder_id)
        if reminder is not None:
            await self.scheduler.register_reminder_job(reminder)
        return SkillOutput(
            status="success",
            result={
                "reminder_id": reminder_id,
                "status": "active",
            },
        )

    async def _list_reminders(self, params: dict[str, Any]) -> SkillOutput:
        status = params.get("status")
        status_filter: str | None = None
        if status is not None:
            cleaned = str(status).strip().lower()
            if cleaned and cleaned not in {"all", "*"}:
                status_filter = cleaned
        rows = await self.structured_store.list_reminders(status=status_filter)
        return SkillOutput(status="success", result={"items": rows})

    async def _delete_reminder(self, params: dict[str, Any]) -> SkillOutput:
        reminder_id = self._read_reminder_id(params)
        if reminder_id is None:
            return SkillOutput(status="error", error_info="reminder_id is required")

        await self.structured_store.delete_reminder(reminder_id)
        await self.scheduler.remove_reminder_job(reminder_id)
        return SkillOutput(status="success", result={"reminder_id": reminder_id, "status": "deleted"})

    async def _update_reminder(self, params: dict[str, Any]) -> SkillOutput:
        reminder_id = self._read_reminder_id(params)
        if reminder_id is None:
            return SkillOutput(status="error", error_info="reminder_id is required")

        kwargs: dict[str, Any] = {}
        for key in (
            "title",
            "description",
            "schedule_type",
            "schedule_value",
            "channel",
            "status",
            "next_run_at",
        ):
            if key in params:
                kwargs[key] = params.get(key)
        if "heartbeat_config" in params:
            kwargs["heartbeat_config"] = self._json_dumps_or_none(params.get("heartbeat_config"))

        await self.structured_store.update_reminder(reminder_id, **kwargs)
        updated = await self.structured_store.get_reminder(reminder_id)
        if updated is not None and updated.get("status") == "active":
            await self.scheduler.register_reminder_job(updated)
        else:
            await self.scheduler.remove_reminder_job(reminder_id)
        return SkillOutput(status="success", result={"reminder_id": reminder_id})

    async def _snooze_reminder(self, params: dict[str, Any]) -> SkillOutput:
        reminder_id = self._read_reminder_id(params)
        if reminder_id is None:
            return SkillOutput(status="error", error_info="reminder_id is required")
        duration_text = str(params.get("duration") or "").strip()
        if not duration_text:
            return SkillOutput(status="error", error_info="duration is required")

        reminder = await self.structured_store.get_reminder(reminder_id)
        if reminder is None:
            return SkillOutput(status="error", error_info="reminder not found")

        delta = self._parse_duration(duration_text)
        if delta is None:
            return SkillOutput(status="error", error_info="unsupported duration format")

        run_at = datetime.now(self._default_timezone()) + delta
        run_at_iso = run_at.isoformat()

        await self.scheduler.remove_reminder_job(reminder_id)
        await self.structured_store.update_reminder(
            reminder_id,
            schedule_type="once",
            schedule_value=run_at_iso,
            next_run_at=run_at_iso,
            status="active",
        )
        updated = await self.structured_store.get_reminder(reminder_id)
        if updated is not None:
            await self.scheduler.register_reminder_job(updated)
        return SkillOutput(
            status="success",
            result={"reminder_id": reminder_id, "snoozed_until": run_at_iso},
        )

    async def _parse_schedule_preview(
        self,
        *,
        schedule_type: str,
        schedule_value: str,
    ) -> dict[str, Any]:
        if self.model_router is None or not hasattr(self.model_router, "call_lightweight_json"):
            return {
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "human_readable": schedule_value,
                "timezone": self._default_timezone_name(),
            }

        prompt = (
            "Parse reminder schedule into JSON with keys: "
            "schedule_type, schedule_value, human_readable, timezone. "
            f"Input schedule_type={schedule_type}, schedule_value={schedule_value}"
        )
        parsed = await self.model_router.call_lightweight_json(prompt)
        if not isinstance(parsed, dict):
            return {
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "human_readable": schedule_value,
                "timezone": self._default_timezone_name(),
            }
        return {
            "schedule_type": str(parsed.get("schedule_type") or schedule_type),
            "schedule_value": str(parsed.get("schedule_value") or schedule_value),
            "human_readable": str(parsed.get("human_readable") or schedule_value),
            "timezone": str(parsed.get("timezone") or self._default_timezone_name()),
        }

    def _parse_confirm(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "ok", "confirm", "confirmed"}:
                return True
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return False

    def _parse_duration(self, raw: str) -> timedelta | None:
        lowered = raw.strip().lower()
        if not lowered:
            return None

        pattern = re.compile(r"^(\d+)\s*(m|min|mins|minute|minutes|分钟|h|hr|hour|hours|小时|d|day|days|天)$")
        match = pattern.match(lowered)
        if match is None:
            return None

        amount = int(match.group(1))
        unit = match.group(2)
        if unit in {"m", "min", "mins", "minute", "minutes", "分钟"}:
            return timedelta(minutes=amount)
        if unit in {"h", "hr", "hour", "hours", "小时"}:
            return timedelta(hours=amount)
        if unit in {"d", "day", "days", "天"}:
            return timedelta(days=amount)
        return None

    def _ensure_timezone(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self._default_timezone())
        return value

    def _json_dumps_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return None

    def _read_reminder_id(self, params: dict[str, Any]) -> int | None:
        raw = params.get("reminder_id")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _default_timezone_name(self) -> str:
        value = getattr(self.scheduler, "default_timezone", None)
        if not isinstance(value, str):
            return "UTC"
        cleaned = value.strip()
        return cleaned if cleaned else "UTC"

    def _default_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._default_timezone_name())
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")
