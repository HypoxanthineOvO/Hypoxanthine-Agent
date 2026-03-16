from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog

from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.heartbeat")

SILENT_SENTINEL = "**SILENT**"


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
        timeout_seconds: int = 240,
    ) -> None:
        self.message_queue = message_queue
        self.scheduler = scheduler
        self.default_session_id = default_session_id
        self.prompt_path = Path(prompt_path)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.last_heartbeat_at: str | None = None
        self._prompt_missing_reported = False

    def _load_prompt_text(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except Exception:
            logger.exception("heartbeat.prompt.load_failed", path=str(self.prompt_path))
            return ""

    async def run(self) -> dict[str, Any]:
        started_at = perf_counter()
        prompt = self._load_prompt_text()
        if not prompt:
            summary = f"Heartbeat 配置异常：未找到或内容为空 - {self.prompt_path.as_posix()}"
            self.last_heartbeat_at = datetime.now(UTC).isoformat()
            if not self._prompt_missing_reported:
                self._prompt_missing_reported = True
                await self._push_summary(summary)
                return {"should_push": True, "summary": summary, "error": "prompt_missing"}
            logger.warning("heartbeat.prompt.missing", path=str(self.prompt_path))
            return {"should_push": False, "summary": SILENT_SENTINEL, "error": "prompt_missing"}

        inbound = Message(
            text=prompt,
            sender="user",
            session_id=self.default_session_id,
            channel="system",
            message_tag="heartbeat",
            metadata={"source": "heartbeat"},
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

        await self.message_queue.put(
            {
                "event_type": "user_message",
                "message": inbound,
                "emit": emit,
            }
        )

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
            self.last_heartbeat_at = datetime.now(UTC).isoformat()
            logger.warning("heartbeat.timeout", timeout_seconds=self.timeout_seconds)
            return {"should_push": True, "summary": summary, "error": "timeout"}
        finally:
            self.last_heartbeat_at = datetime.now(UTC).isoformat()

        duration_ms = int((perf_counter() - started_at) * 1000)

        if error_payload is not None:
            error_message = str(error_payload.get("message") or "").strip() or "unknown error"
            summary = f"Heartbeat 执行失败：{error_message}"
            await self._push_summary(summary)
            logger.warning(
                "heartbeat.failed",
                duration_ms=duration_ms,
                error=error_message,
            )
            return {"should_push": True, "summary": summary, "error": "pipeline_error"}

        normalized = assistant_text.strip()
        if normalized == SILENT_SENTINEL:
            logger.info("heartbeat.silent", duration_ms=duration_ms)
            return {"should_push": False, "summary": SILENT_SENTINEL}

        if not normalized:
            logger.info("heartbeat.empty_reply", duration_ms=duration_ms)
            return {"should_push": False, "summary": SILENT_SENTINEL}

        await self._push_summary(normalized)
        logger.info("heartbeat.push", duration_ms=duration_ms)
        return {"should_push": True, "summary": normalized}

    async def _push_summary(self, summary: str) -> None:
        cleaned = str(summary or "").strip()
        if not cleaned:
            return
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
            "active_tasks": active_tasks,
        }

