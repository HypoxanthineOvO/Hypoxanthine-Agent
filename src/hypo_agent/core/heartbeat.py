from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog

from hypo_agent.models import Message
from hypo_agent.utils.timeutil import now_iso

logger = structlog.get_logger("hypo_agent.heartbeat")

SILENT_SENTINEL = "**SILENT**"
_HEARTBEAT_QUEUE_ERRORS = (asyncio.QueueFull, OSError, RuntimeError, TypeError, ValueError)
_HEARTBEAT_CALLBACK_ERRORS = (
    asyncio.TimeoutError,
    TimeoutError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class HeartbeatService:
    """Prompt-driven heartbeat (OpenClaw style).

    The heartbeat job enqueues an internal user_message event into the central queue and lets
    the normal pipeline ReAct loop drive tool calls and reasoning. The agent must output
    `**SILENT**` exactly when there is nothing worth pushing.
    """

    def __init__(
        self,
        *,
        message_queue: Any,
        scheduler: Any | None = None,
        default_session_id: str = "main",
        prompt_path: Path | str = "config/heartbeat_prompt.md",
        timeout_seconds: int = 120,
        snapshot_provider: Any | None = None,
    ) -> None:
        self.message_queue = message_queue
        self.scheduler = scheduler
        self.default_session_id = default_session_id
        self.prompt_path = Path(prompt_path)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.last_heartbeat_at: str | None = None
        self.last_success_at: str | None = None
        self.consecutive_failures = 0
        self._prompt_missing_reported = False
        self._run_lock = asyncio.Lock()
        self._event_sources: dict[str, Any] = {}
        self._snapshot_provider = snapshot_provider

    def _load_prompt_text(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError:
            logger.exception("heartbeat.prompt.load_failed", path=str(self.prompt_path))
            return ""

    def register_event_source(self, name: str, callback: Any) -> None:
        source_name = str(name or "").strip()
        if not source_name:
            raise ValueError("event source name is required")
        if not callable(callback):
            raise TypeError("event source callback must be callable")
        self._event_sources[source_name] = callback

    def configure_snapshot_provider(self, callback: Any | None) -> None:
        self._snapshot_provider = callback

    async def run(self) -> dict[str, Any]:
        if self._run_lock.locked():
            logger.warning(
                "heartbeat.skipped_overlap",
                session_id=self.default_session_id,
            )
            return {
                "should_push": False,
                "summary": SILENT_SENTINEL,
                "error": "overlap_skipped",
                "event_sources": {},
            }

        async with self._run_lock:
            return await self._run_unlocked()

    async def _run_unlocked(self) -> dict[str, Any]:
        started_at = perf_counter()
        logger.info(
            "heartbeat.start",
            session_id=self.default_session_id,
            prompt_path=str(self.prompt_path),
            timeout_seconds=self.timeout_seconds,
        )
        prompt = self._load_prompt_text()
        if not prompt:
            summary = f"Heartbeat 配置异常：未找到或内容为空 - {self.prompt_path.as_posix()}"
            self.last_heartbeat_at = now_iso()
            logger.error(
                "heartbeat.prompt.missing",
                session_id=self.default_session_id,
                path=str(self.prompt_path),
                already_reported=self._prompt_missing_reported,
            )
            if not self._prompt_missing_reported:
                self._prompt_missing_reported = True
                await self._push_summary(summary)
                self._mark_failure()
                return {
                    "should_push": True,
                    "summary": summary,
                    "error": "prompt_missing",
                    "event_sources": {},
                }
            self._mark_failure()
            return {
                "should_push": False,
                "summary": SILENT_SENTINEL,
                "error": "prompt_missing",
                "event_sources": {},
            }

        snapshot_payload = await self._collect_snapshot_payload()
        snapshot_context = self._render_snapshot_context(snapshot_payload)
        if snapshot_context:
            prompt = (
                f"{prompt}\n\n## 预取心跳快照\n"
                f"{snapshot_context}\n\n"
                "上面是已经整理好的 heartbeat 细节。"
                "如果后续工具结果一致，优先保留这些细节，不要再压缩成一句空话。"
            )

        event_source_context, event_source_statuses = await self._collect_event_source_context()
        if event_source_context:
            prompt = (
                f"{prompt}\n\n## 外部事件源预检\n"
                f"{event_source_context}\n\n"
                "这些是额外线索，不替代你自行调用工具核实。"
            )

        inbound = Message(
            text=prompt,
            sender="user",
            session_id=self.default_session_id,
            channel="system",
            message_tag="heartbeat",
            metadata={"source": "heartbeat", "skip_memory_search": True},
        )

        loop = asyncio.get_running_loop()
        done_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        chunks: list[str] = []

        async def emit(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict):
                return
            payload_type = str(payload.get("type") or "").strip().lower()
            if payload_type == "assistant_chunk":
                chunks.append(str(payload.get("text") or ""))
                return
            if payload_type == "assistant_done":
                if not done_future.done():
                    done_future.set_result({"ok": True})
                return
            if payload_type == "error":
                if not done_future.done():
                    done_future.set_result({"ok": False, "error": dict(payload)})

        try:
            await self.message_queue.put(
                {
                    "event_type": "user_message",
                    "message": inbound,
                    "emit": emit,
                }
            )
        except _HEARTBEAT_QUEUE_ERRORS:
            logger.exception(
                "heartbeat.enqueue.failed",
                session_id=self.default_session_id,
                event_type="user_message",
            )
            raise

        assistant_text = ""
        error_payload: dict[str, Any] | None = None
        try:
            result_payload = await asyncio.wait_for(done_future, timeout=self.timeout_seconds)
            if not bool(result_payload.get("ok", True)):
                error_payload = result_payload.get("error")
            assistant_text = "".join(chunks).strip()
        except TimeoutError:
            summary = "Heartbeat 检查超时（可能是模型/工具调用异常）。请查看日志或稍后重试。"
            await self._push_summary(summary)
            self.last_heartbeat_at = now_iso()
            self._mark_failure()
            logger.error(
                "heartbeat.timeout",
                session_id=self.default_session_id,
                timeout_seconds=self.timeout_seconds,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
            return {
                "should_push": True,
                "summary": summary,
                "error": "timeout",
                "event_sources": event_source_statuses,
            }
        finally:
            self.last_heartbeat_at = now_iso()

        duration_ms = int((perf_counter() - started_at) * 1000)

        if error_payload is not None:
            error_message = str(error_payload.get("message") or "").strip() or "unknown error"
            summary = f"Heartbeat 执行失败：{error_message}"
            await self._push_summary(summary)
            self._mark_failure()
            logger.error(
                "heartbeat.failed",
                session_id=self.default_session_id,
                duration_ms=duration_ms,
                error=error_message,
            )
            return {
                "should_push": True,
                "summary": summary,
                "error": "pipeline_error",
                "event_sources": event_source_statuses,
            }

        normalized = assistant_text.strip()
        if normalized == SILENT_SENTINEL:
            self._mark_success()
            logger.info(
                "heartbeat.silent",
                session_id=self.default_session_id,
                duration_ms=duration_ms,
            )
            return {
                "should_push": False,
                "summary": SILENT_SENTINEL,
                "event_sources": event_source_statuses,
            }

        if not normalized:
            self._mark_success()
            logger.info(
                "heartbeat.empty_reply",
                session_id=self.default_session_id,
                duration_ms=duration_ms,
            )
            return {
                "should_push": False,
                "summary": SILENT_SENTINEL,
                "event_sources": event_source_statuses,
            }

        if snapshot_payload:
            fallback = self._render_snapshot_push_text(snapshot_payload)
            if fallback:
                normalized = fallback

        self._mark_success()
        await self._push_summary(normalized)
        logger.info(
            "heartbeat.push",
            session_id=self.default_session_id,
            duration_ms=duration_ms,
        )
        return {
            "should_push": True,
            "summary": normalized,
            "event_sources": event_source_statuses,
        }

    async def _push_summary(self, summary: str) -> None:
        cleaned = str(summary or "").strip()
        if not cleaned:
            return
        try:
            await self.message_queue.put(
                {
                    "event_type": "heartbeat_trigger",
                    "session_id": self.default_session_id,
                    "message_tag": "heartbeat",
                    "summary": cleaned,
                    "title": "heartbeat",
                    "description": cleaned,
                }
            )
        except _HEARTBEAT_QUEUE_ERRORS:
            logger.exception(
                "heartbeat.enqueue.failed",
                session_id=self.default_session_id,
                event_type="heartbeat_trigger",
            )
            raise

    def get_status(self, *, scheduler: Any | None = None) -> dict[str, Any]:
        runtime_scheduler = scheduler or self.scheduler
        running = bool(getattr(runtime_scheduler, "is_running", False))
        has_job = False
        active_tasks = 0
        if runtime_scheduler is not None and hasattr(runtime_scheduler, "has_job_id"):
            has_job = bool(runtime_scheduler.has_job_id("heartbeat"))
        if runtime_scheduler is not None and hasattr(runtime_scheduler, "get_active_job_count"):
            active_tasks = int(runtime_scheduler.get_active_job_count())
        return {
            "status": "running" if running and (has_job or active_tasks > 0) else "disabled",
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_success_at": self.last_success_at,
            "consecutive_failures": self.consecutive_failures,
            "active_tasks": active_tasks,
        }

    def _mark_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success_at = now_iso()

    def _mark_failure(self) -> None:
        self.consecutive_failures += 1

    async def _collect_snapshot_payload(self) -> dict[str, Any] | None:
        if not callable(self._snapshot_provider):
            return None
        try:
            payload = self._snapshot_provider()
            if inspect.isawaitable(payload):
                payload = await payload
        except _HEARTBEAT_CALLBACK_ERRORS as exc:
            logger.warning(
                "heartbeat.snapshot_provider.failed",
                error=str(exc).strip() or exc.__class__.__name__,
            )
            return None
        return payload if isinstance(payload, dict) else None

    def _render_snapshot_context(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        sections: list[str] = []
        for key, title in (
            ("mail", "mail"),
            ("notion_todo", "notion_todo"),
            ("reminders", "reminders"),
        ):
            section = payload.get(key)
            if not isinstance(section, dict):
                continue
            if not self._snapshot_section_has_signal(key, section):
                continue
            human_summary = str(section.get("human_summary") or "").strip()
            if human_summary:
                sections.append(f"### {title}\n{human_summary}")
        return "\n\n".join(sections).strip()

    def _render_snapshot_push_text(self, payload: dict[str, Any]) -> str:
        sections: list[str] = ["心跳检查完成。"]
        for key, title in (
            ("mail", "邮件"),
            ("notion_todo", "Notion 待办"),
            ("reminders", "提醒"),
        ):
            section = payload.get(key)
            if not isinstance(section, dict):
                continue
            if not self._snapshot_section_has_signal(key, section):
                continue
            human_summary = str(section.get("human_summary") or "").strip()
            if not human_summary:
                error = str(section.get("error") or "").strip()
                if error:
                    human_summary = error
            if human_summary:
                sections.append(f"{title}\n{human_summary}")
        if len(sections) == 1:
            return ""
        return "\n\n".join(part.strip() for part in sections if part.strip()).strip()

    def _snapshot_section_has_signal(self, key: str, section: dict[str, Any]) -> bool:
        if str(section.get("error") or "").strip():
            return True
        if key == "mail":
            return bool(section.get("important") or section.get("other") or int(section.get("new_emails") or 0) > 0)
        if key == "notion_todo":
            return bool(
                section.get("pending_today")
                or section.get("high_priority_due_soon")
                or section.get("completed_today")
                or section.get("candidate")
                or section.get("candidates")
            )
        if key == "reminders":
            return bool(section.get("overdue") or section.get("due_soon"))
        return False

    async def _collect_event_source_context(self) -> tuple[str, dict[str, dict[str, str | None]]]:
        if not self._event_sources:
            return "", {}
        blocks: list[str] = []
        statuses: dict[str, dict[str, str | None]] = {}
        for name, callback in self._event_sources.items():
            try:
                payload = callback()
                if inspect.isawaitable(payload):
                    payload = await payload
            except _HEARTBEAT_CALLBACK_ERRORS as exc:
                status = "timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "failed"
                error_message = str(exc).strip() or exc.__class__.__name__
                statuses[name] = {"status": status, "error": error_message}
                logger.warning(
                    "heartbeat.event_source.failed",
                    source=name,
                    status=status,
                    error=error_message,
                )
                continue
            statuses[name] = {"status": "success", "error": None}
            rendered = self._render_event_source_payload(name=name, payload=payload)
            if rendered:
                blocks.append(rendered)
        return "\n\n".join(blocks).strip(), statuses

    def _render_event_source_payload(self, *, name: str, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            cleaned = payload.strip()
            return f"### {name}\n- {cleaned}" if cleaned else ""
        if not isinstance(payload, dict):
            return ""

        lines = [f"### {name}"]
        new_items = payload.get("new_items")
        if new_items is not None:
            lines.append(f"- new_items: {new_items}")
        items = payload.get("items")
        if isinstance(items, list):
            for item in items[:5]:
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("summary") or "").strip()
                    subscription = str(item.get("subscription") or "").strip()
                    platform = str(item.get("platform") or "").strip()
                    if title:
                        prefix = "订阅命中" if subscription else "条目"
                        details = " / ".join(part for part in (subscription, platform) if part)
                        if details:
                            lines.append(f"- {prefix}: {title} ({details})")
                        else:
                            lines.append(f"- {prefix}: {title}")
                else:
                    text = str(item).strip()
                    if text:
                        lines.append(f"- {text}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)
