from __future__ import annotations

import asyncio
from datetime import datetime
import os
from pathlib import Path
from urllib import request as urllib_request
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_MISSED, JobEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import structlog

from hypo_agent.core.event_queue import EventQueue

logger = structlog.get_logger("hypo_agent.scheduler")


class SchedulerService:
    def __init__(
        self,
        *,
        structured_store: Any,
        event_queue: EventQueue,
        default_session_id: str = "main",
        default_timezone: str | None = None,
        model_router: Any | None = None,
        misfire_grace_time_seconds: int = 30,
    ) -> None:
        self.structured_store = structured_store
        self.event_queue = event_queue
        self.default_session_id = default_session_id
        self.default_timezone = self._resolve_timezone_name(default_timezone)
        self.model_router = model_router
        self._misfire_grace_time_seconds = max(1, int(misfire_grace_time_seconds))
        self._scheduler = AsyncIOScheduler(timezone=self.default_timezone)
        self._scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._scheduler.start()
        self._running = True
        await self.reload_active_jobs()
        await self._sweep_expired_once_reminders()

    async def stop(self) -> None:
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False

    async def reload_active_jobs(self) -> None:
        reminders = await self.structured_store.list_reminders(status="active")
        for reminder in reminders:
            try:
                await self.register_reminder_job(reminder)
            except Exception:
                logger.exception(
                    "scheduler.restore.failed",
                    reminder_id=reminder.get("id"),
                    schedule_type=reminder.get("schedule_type"),
                    schedule_value=reminder.get("schedule_value"),
                )

    async def register_reminder_job(self, reminder: dict[str, Any]) -> None:
        reminder_id = int(reminder["id"])
        job_id = self._job_id(reminder_id)
        trigger = self._build_trigger(reminder)
        self._remove_job_if_exists(job_id)
        self._scheduler.add_job(
            self._handle_job_trigger,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs={"reminder_id": reminder_id},
            misfire_grace_time=self._misfire_grace_time_seconds,
        )
        job = self._scheduler.get_job(job_id)
        if job is not None and hasattr(self.structured_store, "set_reminder_next_run_at"):
            next_run = getattr(job, "next_run_time", None)
            next_run_iso = next_run.isoformat() if isinstance(next_run, datetime) else None
            await self.structured_store.set_reminder_next_run_at(reminder_id, next_run_iso)
            logger.info(
                "scheduler.job_registered",
                reminder_id=reminder_id,
                trigger_time=next_run_iso,
                timezone=str(getattr(next_run, "tzinfo", None) or self.default_timezone),
            )
        else:
            logger.info(
                "scheduler.job_registered",
                reminder_id=reminder_id,
                trigger_time=None,
                timezone=self.default_timezone,
            )

    async def remove_reminder_job(self, reminder_id: int) -> None:
        self._remove_job_if_exists(self._job_id(reminder_id))

    def has_job(self, reminder_id: int) -> bool:
        return self._scheduler.get_job(self._job_id(reminder_id)) is not None

    async def _sweep_expired_once_reminders(self) -> None:
        active_reminders = await self.structured_store.list_reminders(status="active")
        now = datetime.now(tz=self._timezone_obj())
        for reminder in active_reminders:
            if str(reminder.get("schedule_type") or "").lower() != "once":
                continue
            reminder_id = int(reminder.get("id") or 0)
            if reminder_id <= 0:
                continue
            trigger_time = self._parse_once_schedule(
                schedule_value=str(reminder.get("schedule_value") or "").strip()
            )
            if trigger_time is None:
                continue
            if trigger_time < now:
                await self._mark_reminder_missed(
                    reminder_id=reminder_id,
                    trigger_time=trigger_time.isoformat(),
                    source="expired_sweep",
                )

    @staticmethod
    def parse_cron_schedule(
        schedule_value: str,
        *,
        default_timezone: str,
    ) -> tuple[str, str]:
        raw = schedule_value.strip()
        if raw.startswith("CRON_TZ="):
            parts = raw.split(maxsplit=1)
            if len(parts) == 2:
                tz_part, expression = parts
                timezone = tz_part.split("=", maxsplit=1)[1].strip() or default_timezone
                return expression.strip(), timezone
        return raw, default_timezone

    async def _handle_job_trigger(self, *, reminder_id: int) -> None:
        reminder: dict[str, Any] | None = None
        if hasattr(self.structured_store, "get_reminder"):
            reminder = await self.structured_store.get_reminder(reminder_id)
        payload = reminder or {}

        heartbeat_config = payload.get("heartbeat_config")
        if isinstance(heartbeat_config, list) and heartbeat_config:
            await self._handle_heartbeat_trigger(reminder_id=reminder_id, reminder=payload)
        else:
            event = {
                "event_type": "reminder_trigger",
                "reminder_id": reminder_id,
                "session_id": self.default_session_id,
                "title": payload.get("title", ""),
                "description": payload.get("description"),
                "channel": payload.get("channel", "all"),
                "schedule_type": payload.get("schedule_type", "once"),
            }
            await self.event_queue.put(event)
            logger.info("scheduler.triggered", reminder_id=reminder_id, event_type="reminder_trigger")

        if str(payload.get("schedule_type") or "").lower() == "once":
            if hasattr(self.structured_store, "mark_reminder_completed"):
                await self.structured_store.mark_reminder_completed(reminder_id)
            self._remove_job_if_exists(self._job_id(reminder_id))

    async def _handle_heartbeat_trigger(
        self,
        *,
        reminder_id: int,
        reminder: dict[str, Any],
    ) -> None:
        checks = reminder.get("heartbeat_config") or []
        precheck_results = await self._run_heartbeat_prechecks(checks)
        decision = await self._decide_heartbeat(
            reminder=reminder,
            precheck_results=precheck_results,
        )
        if decision == "normal":
            logger.info(
                "scheduler.heartbeat.silent",
                reminder_id=reminder_id,
                precheck_results=precheck_results,
            )
            return

        event = {
            "event_type": "heartbeat_trigger",
            "reminder_id": reminder_id,
            "session_id": self.default_session_id,
            "title": reminder.get("title", ""),
            "description": reminder.get("description"),
            "channel": reminder.get("channel", "all"),
            "schedule_type": reminder.get("schedule_type", "cron"),
            "heartbeat_results": precheck_results,
        }
        await self.event_queue.put(event)
        logger.info("scheduler.triggered", reminder_id=reminder_id, event_type="heartbeat_trigger")

    def _build_trigger(self, reminder: dict[str, Any]) -> DateTrigger | CronTrigger:
        schedule_type = str(reminder.get("schedule_type") or "").lower()
        schedule_value = str(reminder.get("schedule_value") or "").strip()
        if schedule_type == "once":
            run_at = self._parse_once_schedule(schedule_value=schedule_value)
            if run_at is None:
                raise ValueError("Invalid once schedule_value")
            return DateTrigger(run_date=run_at)
        if schedule_type == "cron":
            expression, timezone = self.parse_cron_schedule(
                schedule_value,
                default_timezone=str(reminder.get("timezone") or self.default_timezone),
            )
            return CronTrigger.from_crontab(expression, timezone=timezone)
        raise ValueError(f"Unsupported schedule_type '{schedule_type}'")

    def _on_job_missed(self, event: JobEvent) -> None:
        reminder_id = self._reminder_id_from_job_id(str(getattr(event, "job_id", "") or ""))
        if reminder_id is None:
            return

        logger.warning("scheduler.job_missed_event", reminder_id=reminder_id, job_id=event.job_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._mark_reminder_missed(
                reminder_id=reminder_id,
                trigger_time=None,
                source="job_missed_event",
            )
        )
        task.add_done_callback(self._log_background_task_error)

    async def _run_heartbeat_prechecks(
        self,
        checks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for check in checks:
            check_type = str(check.get("check_type") or "").strip().lower()
            target = str(check.get("target") or "")
            expected = check.get("expected")
            timeout_seconds = int(check.get("timeout_seconds") or 10)

            if check_type == "file_exists":
                passed = Path(target).exists() if target else False
                results.append(
                    {
                        "check_type": check_type,
                        "target": target,
                        "passed": passed,
                        "observed": "exists" if passed else "missing",
                    }
                )
                continue

            if check_type == "process_running":
                command = ["pgrep", "-f", target] if target else ["false"]
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout_seconds,
                    )
                    passed = process.returncode == 0
                    results.append(
                        {
                            "check_type": check_type,
                            "target": target,
                            "passed": passed,
                            "observed": stdout.decode("utf-8", errors="replace").strip(),
                            "stderr": stderr.decode("utf-8", errors="replace").strip(),
                        }
                    )
                except TimeoutError:
                    process.kill()
                    await process.communicate()
                    results.append(
                        {
                            "check_type": check_type,
                            "target": target,
                            "passed": False,
                            "observed": "timeout",
                        }
                    )
                continue

            if check_type == "http_status":
                status_code = await self._fetch_http_status(target, timeout_seconds)
                expected_status = int(expected if expected is not None else 200)
                results.append(
                    {
                        "check_type": check_type,
                        "target": target,
                        "passed": status_code == expected_status,
                        "observed": status_code,
                        "expected": expected_status,
                    }
                )
                continue

            if check_type == "custom_command":
                process = await asyncio.create_subprocess_shell(
                    target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout_seconds,
                    )
                    expected_rc = int(expected if expected is not None else 0)
                    passed = process.returncode == expected_rc
                    results.append(
                        {
                            "check_type": check_type,
                            "target": target,
                            "passed": passed,
                            "observed": process.returncode,
                            "stdout": stdout.decode("utf-8", errors="replace").strip(),
                            "stderr": stderr.decode("utf-8", errors="replace").strip(),
                            "expected": expected_rc,
                        }
                    )
                except TimeoutError:
                    process.kill()
                    await process.communicate()
                    results.append(
                        {
                            "check_type": check_type,
                            "target": target,
                            "passed": False,
                            "observed": "timeout",
                        }
                    )
                continue

            results.append(
                {
                    "check_type": check_type or "unknown",
                    "target": target,
                    "passed": False,
                    "observed": "unsupported_check_type",
                }
            )
        return results

    async def _decide_heartbeat(
        self,
        *,
        reminder: dict[str, Any],
        precheck_results: list[dict[str, Any]],
    ) -> str:
        if self.model_router is not None and hasattr(self.model_router, "call_lightweight_json"):
            prompt = (
                "You are heartbeat judge. Reply strict JSON: {\"decision\":\"normal|abnormal\"}. "
                "Decision should consider the checks below.\n"
                f"Reminder: {reminder.get('title', '')}\n"
                f"Checks: {precheck_results}"
            )
            payload = await self.model_router.call_lightweight_json(prompt)
            decision = str(payload.get("decision") or "").strip().lower()
            if decision in {"normal", "ok", "healthy"}:
                return "normal"
            return "abnormal"

        all_passed = all(bool(item.get("passed")) for item in precheck_results)
        return "normal" if all_passed else "abnormal"

    async def _fetch_http_status(self, target: str, timeout_seconds: int) -> int | None:
        if not target:
            return None

        def _do_request() -> int | None:
            try:
                with urllib_request.urlopen(target, timeout=timeout_seconds) as resp:
                    return int(getattr(resp, "status", None))
            except Exception:
                return None

        return await asyncio.to_thread(_do_request)

    def _remove_job_if_exists(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            return

    def _job_id(self, reminder_id: int) -> str:
        return f"reminder:{reminder_id}"

    def _reminder_id_from_job_id(self, job_id: str) -> int | None:
        if not job_id.startswith("reminder:"):
            return None
        raw = job_id.split(":", maxsplit=1)[1]
        try:
            reminder_id = int(raw)
        except ValueError:
            return None
        return reminder_id if reminder_id > 0 else None

    def _parse_once_schedule(self, *, schedule_value: str) -> datetime | None:
        if not schedule_value:
            return None
        try:
            parsed = datetime.fromisoformat(schedule_value)
        except ValueError:
            logger.warning("scheduler.invalid_once_schedule", schedule_value=schedule_value)
            return None
        timezone = self._timezone_obj()
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    async def _mark_reminder_missed(
        self,
        *,
        reminder_id: int,
        trigger_time: str | None,
        source: str,
    ) -> None:
        reminder: dict[str, Any] | None = None
        if hasattr(self.structured_store, "get_reminder"):
            reminder = await self.structured_store.get_reminder(reminder_id)
        if reminder is None:
            return
        if str(reminder.get("schedule_type") or "").strip().lower() != "once":
            return
        if str(reminder.get("status") or "").strip().lower() != "active":
            return

        if hasattr(self.structured_store, "update_reminder"):
            await self.structured_store.update_reminder(
                reminder_id,
                status="missed",
            )
        if hasattr(self.structured_store, "set_reminder_next_run_at"):
            await self.structured_store.set_reminder_next_run_at(reminder_id, None)
        self._remove_job_if_exists(self._job_id(reminder_id))
        logger.info(
            "scheduler.expired_sweep",
            reminder_id=reminder_id,
            was_scheduled=trigger_time,
            source=source,
        )

    def _log_background_task_error(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        logger.exception("scheduler.background_task_failed", error=str(error))

    def _timezone_obj(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _resolve_timezone_name(self, configured: str | None) -> str:
        candidates = [
            configured,
            os.getenv("HYPO_AGENT_TIMEZONE"),
            os.getenv("TZ"),
            "Asia/Shanghai",
            "UTC",
        ]
        for item in candidates:
            if not item:
                continue
            value = str(item).strip()
            if not value:
                continue
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError:
                continue
            return value
        return "UTC"
