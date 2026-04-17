from __future__ import annotations

import asyncio
from datetime import datetime
import imaplib
import inspect
import os
from pathlib import Path
from urllib import request as urllib_request
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_MISSED, JobEvent
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
import structlog

from hypo_agent.core.event_queue import EventQueue

logger = structlog.get_logger("hypo_agent.scheduler")
_SCHEDULER_RECOVERABLE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_EMAIL_SCAN_RECOVERABLE_ERRORS = (
    imaplib.IMAP4.error,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


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
        self._interval_job_ids: set[str] = set()
        self._email_scan_executor: Any | None = None

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
        for job_id in list(self._interval_job_ids):
            self._remove_job_if_exists(job_id)
        self._interval_job_ids.clear()
        self._scheduler.shutdown(wait=False)
        self._running = False

    async def reload_active_jobs(self) -> None:
        reminders = await self.structured_store.list_reminders(status="active")
        for reminder in reminders:
            try:
                await self.register_reminder_job(reminder)
            except _SCHEDULER_RECOVERABLE_ERRORS:
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

    def has_job_id(self, job_id: str) -> bool:
        return self._scheduler.get_job(str(job_id)) is not None

    def get_job_next_run_iso(self, job_id: str) -> str | None:
        job = self._scheduler.get_job(str(job_id))
        if job is None:
            return None
        next_run = getattr(job, "next_run_time", None)
        return next_run.isoformat() if isinstance(next_run, datetime) else None

    def get_active_job_count(self) -> int:
        if not self._running:
            return 0
        return len(self._scheduler.get_jobs())

    def register_interval_job(
        self,
        job_id: str,
        minutes: int,
        coro: Any,
        *,
        replace_existing: bool = True,
    ) -> None:
        if not callable(coro):
            raise TypeError("coro must be callable")
        safe_minutes = max(1, int(minutes))
        trigger = IntervalTrigger(minutes=safe_minutes, timezone=self.default_timezone)
        self._scheduler.add_job(
            coro,
            trigger=trigger,
            id=str(job_id),
            replace_existing=replace_existing,
            misfire_grace_time=self._misfire_grace_time_seconds,
        )
        self._interval_job_ids.add(str(job_id))
        logger.info(
            "scheduler.interval_job_registered",
            job_id=str(job_id),
            interval_minutes=safe_minutes,
        )

    def register_subscription_job(
        self,
        job_id: str,
        coro: Any,
        *,
        interval_seconds: int,
        jitter_seconds: int = 0,
        replace_existing: bool = True,
    ) -> None:
        if not callable(coro):
            raise TypeError("coro must be callable")
        safe_seconds = max(60, int(interval_seconds))
        safe_jitter = max(0, int(jitter_seconds))
        trigger = IntervalTrigger(
            seconds=safe_seconds,
            timezone=self.default_timezone,
            jitter=safe_jitter or None,
        )
        self._scheduler.add_job(
            coro,
            trigger=trigger,
            id=str(job_id),
            replace_existing=replace_existing,
            misfire_grace_time=self._misfire_grace_time_seconds,
        )
        self._interval_job_ids.add(str(job_id))
        logger.info(
            "scheduler.subscription_job_registered",
            job_id=str(job_id),
            interval_seconds=safe_seconds,
            jitter_seconds=safe_jitter,
        )

    def remove_subscription_job(self, job_id: str) -> None:
        self._remove_job_if_exists(str(job_id))

    def register_cron_job(
        self,
        job_id: str,
        cron: str,
        coro: Any,
        *,
        replace_existing: bool = True,
        timezone: str | None = None,
    ) -> None:
        if not callable(coro):
            raise TypeError("coro must be callable")
        expression = str(cron or "").strip()
        if not expression:
            raise ValueError("cron is required")
        trigger = CronTrigger.from_crontab(
            expression,
            timezone=timezone or self.default_timezone,
        )
        self._scheduler.add_job(
            coro,
            trigger=trigger,
            id=str(job_id),
            replace_existing=replace_existing,
            misfire_grace_time=self._misfire_grace_time_seconds,
        )
        logger.info(
            "scheduler.cron_job_registered",
            job_id=str(job_id),
            cron=expression,
            timezone=timezone or self.default_timezone,
        )

    def set_email_scan_executor(self, executor: Any | None) -> None:
        if executor is not None and not callable(executor):
            raise TypeError("email scan executor must be callable")
        self._email_scan_executor = executor

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

        if self._is_legacy_email_push_heartbeat(payload):
            await self._handle_legacy_email_push_heartbeat(reminder_id=reminder_id, reminder=payload)
        else:
            heartbeat_config = payload.get("heartbeat_config")
            if isinstance(heartbeat_config, list) and heartbeat_config:
                await self._handle_heartbeat_trigger(reminder_id=reminder_id, reminder=payload)
            else:
                event = {
                    "event_type": "reminder_trigger",
                    "reminder_id": reminder_id,
                    "session_id": str(payload.get("session_id") or self.default_session_id),
                    "title": payload.get("title", ""),
                    "description": payload.get("description"),
                    "channel": payload.get("channel", "all"),
                    "schedule_type": payload.get("schedule_type", "once"),
                }
                await self.event_queue.put(event)
                logger.info(
                    "scheduler.triggered",
                    reminder_id=reminder_id,
                    event_type="reminder_trigger",
                )

        if str(payload.get("schedule_type") or "").lower() == "once":
            if hasattr(self.structured_store, "mark_reminder_completed"):
                await self.structured_store.mark_reminder_completed(reminder_id)
            self._remove_job_if_exists(self._job_id(reminder_id))

    async def _handle_legacy_email_push_heartbeat(
        self,
        *,
        reminder_id: int,
        reminder: dict[str, Any],
    ) -> None:
        executor = self._email_scan_executor
        if executor is None:
            logger.info("💓 Heartbeat: 邮件技能未启用，跳过")
            return

        try:
            try:
                result = executor(params={"triggered_by": "heartbeat"})
            except TypeError:
                result = executor()
            if inspect.isawaitable(result):
                result = await result
        except _EMAIL_SCAN_RECOVERABLE_ERRORS as exc:
            if self._is_email_connection_error(exc):
                logger.warning(f"⚠️ Heartbeat: 邮件连接失败，下次重试 - {exc}")
            else:
                logger.exception(
                    "heartbeat.email_scan.unexpected_failed",
                    reminder_id=reminder_id,
                    title=reminder.get("title", ""),
                )
            return

        if not isinstance(result, dict):
            logger.warning(
                "heartbeat.email_scan.invalid_result",
                reminder_id=reminder_id,
                result_type=type(result).__name__,
            )
            return

        accounts_scanned = int(result.get("accounts_scanned") or 0)
        accounts_failed = int(result.get("accounts_failed") or 0)
        new_emails = int(result.get("new_emails") or 0)
        important_count = self._count_important_emails(result)
        failure_error = self._extract_email_failure_error(result)

        if accounts_scanned == 0 and accounts_failed > 0:
            logger.warning(
                f"⚠️ Heartbeat: 邮件连接失败，下次重试 - {failure_error or 'unknown error'}"
            )
            return

        if new_emails <= 0:
            logger.info("💓 Heartbeat: 邮件扫描完成，无新邮件")
            return

        logger.info(
            f"💓 Heartbeat: 邮件扫描完成，{new_emails} 封新邮件（{important_count} 封重要）"
        )
        if important_count <= 0:
            return

        summary = str(result.get("summary") or "").strip()
        if not summary:
            summary = f"📧 邮件扫描完成：{new_emails} 封新邮件，{important_count} 封重要"
        await self.event_queue.put(
            {
                "event_type": "email_scan_trigger",
                "session_id": self.default_session_id,
                "summary": summary,
                "details": result,
                "source": "heartbeat",
            }
        )
        logger.info(
            "scheduler.triggered",
            reminder_id=reminder_id,
            event_type="email_scan_trigger",
        )

    def _is_legacy_email_push_heartbeat(self, reminder: dict[str, Any]) -> bool:
        heartbeat_config = reminder.get("heartbeat_config")
        if not isinstance(heartbeat_config, list):
            return False
        for item in heartbeat_config:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            if action == "push_email_summary":
                return True
        return False

    def _count_important_emails(self, result: dict[str, Any]) -> int:
        items = result.get("items")
        if not isinstance(items, list):
            return 0
        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip().lower()
            if category in {"important", "system"}:
                count += 1
        return count

    def _extract_email_failure_error(self, result: dict[str, Any]) -> str:
        items = result.get("items")
        if not isinstance(items, list):
            return ""
        for item in items:
            if not isinstance(item, dict):
                continue
            error = str(item.get("error") or "").strip()
            if error:
                return error
        return ""

    def _is_email_connection_error(self, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError, OSError, imaplib.IMAP4.error)):
            return True
        lowered = str(exc).strip().lower()
        return any(
            token in lowered
            for token in ("imap", "login", "auth", "ssl", "timeout", "connection")
        )

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
            except (OSError, TimeoutError, ValueError):
                return None

        return await asyncio.to_thread(_do_request)

    def _remove_job_if_exists(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
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
