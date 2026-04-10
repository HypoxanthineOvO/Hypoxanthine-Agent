from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import structlog

from hypo_agent.channels.coder.coder_message_formatter import (
    format_status_message,
    format_terminal_message,
    is_terminal_status,
)
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.channels.coder.stream_watcher")


class CoderStreamWatcher:
    def __init__(
        self,
        *,
        coder_task_service: Any,
        push_callback: Callable[[Message], Awaitable[None] | None],
        poll_interval_seconds: float = 5.0,
        message_char_limit: int = 800,
    ) -> None:
        self.coder_task_service = coder_task_service
        self.push_callback = push_callback
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.message_char_limit = max(80, int(message_char_limit))
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_watching(self, task_id: str) -> bool:
        normalized_task_id = str(task_id or "").strip()
        task = self._tasks.get(normalized_task_id)
        return task is not None and not task.done()

    async def start(
        self,
        *,
        task_id: str,
        session_id: str,
        initial_cursor: str | None = None,
    ) -> bool:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return False
        existing = self._tasks.get(normalized_task_id)
        if existing is not None and not existing.done():
            return False
        self._tasks[normalized_task_id] = asyncio.create_task(
            self._watch(
                task_id=normalized_task_id,
                session_id=session_id,
                initial_cursor=str(initial_cursor or "").strip() or None,
            )
        )
        return True

    async def stop(self, task_id: str) -> None:
        normalized_task_id = str(task_id or "").strip()
        task = self._tasks.pop(normalized_task_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        for task_id in list(self._tasks):
            await self.stop(task_id)

    async def _watch(
        self,
        *,
        task_id: str,
        session_id: str,
        initial_cursor: str | None = None,
    ) -> None:
        last_status = ""
        cursor: str | None = initial_cursor
        try:
            while True:
                attached = await self.coder_task_service.get_attached_task(session_id)
                attached_task_id = str(attached.get("task_id") or "").strip() if attached else ""
                current = await self.coder_task_service.get_task_status(
                    task_id=task_id,
                    session_id=session_id,
                )
                status = str(current.get("status") or "unknown").strip().lower() or "unknown"

                if attached_task_id == task_id and status != last_status:
                    await self._emit_update(
                        session_id=session_id,
                        task_id=task_id,
                        status=status,
                        payload=current,
                    )
                    last_status = status

                if attached_task_id == task_id and self._supports_incremental_output():
                    output = await self._get_incremental_output(task_id=task_id, after=cursor)
                    next_cursor = str(output.get("cursor") or cursor or "").strip() or None
                    lines = output.get("lines") if isinstance(output.get("lines"), list) else []
                    normalized_lines = [str(line) for line in lines if line is not None]
                    if normalized_lines:
                        await self._emit_incremental_output(
                            session_id=session_id,
                            task_id=task_id,
                            status=status,
                            lines=normalized_lines,
                        )
                    cursor = next_cursor
                    if bool(output.get("done")):
                        break

                if is_terminal_status(status):
                    break
                await asyncio.sleep(self.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("coder_stream_watcher.watch_failed", task_id=task_id, session_id=session_id)
        finally:
            self._tasks.pop(task_id, None)

    async def _emit_update(
        self,
        *,
        session_id: str,
        task_id: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if is_terminal_status(status):
            text = format_terminal_message(payload)
        else:
            text = format_status_message(task_id=task_id, status=status)
        if len(text) > self.message_char_limit:
            text = text[: self.message_char_limit - 3] + "..."
        result = self.push_callback(
            Message(
                text=text,
                sender="hypo-coder",
                session_id=session_id,
                channel="system",
                message_tag="tool_status",
                metadata={"source": "hypo_coder", "task_id": task_id, "status": status},
            )
        )
        if hasattr(result, "__await__"):
            await result

    def _supports_incremental_output(self) -> bool:
        checker = getattr(self.coder_task_service, "supports_incremental_output", None)
        return bool(checker()) if callable(checker) else False

    async def _get_incremental_output(
        self,
        *,
        task_id: str,
        after: str | None,
    ) -> dict[str, Any]:
        getter = getattr(self.coder_task_service, "get_task_output", None)
        if not callable(getter):
            return {"cursor": str(after or "").strip(), "lines": [], "done": False}
        result = getter(task_id=task_id, after=after)
        if hasattr(result, "__await__"):
            result = await result
        return dict(result) if isinstance(result, dict) else {"cursor": str(after or "").strip(), "lines": [], "done": False}

    async def _emit_incremental_output(
        self,
        *,
        session_id: str,
        task_id: str,
        status: str,
        lines: list[str],
    ) -> None:
        prefix = f"[Codex | {task_id}]"
        max_body_chars = max(1, self.message_char_limit - len(prefix) - 1)
        body = "\n".join(lines)
        chunks = [body[i : i + max_body_chars] for i in range(0, len(body), max_body_chars)]
        for chunk in chunks:
            await self._push_message(
                session_id=session_id,
                task_id=task_id,
                status=status,
                text=f"{prefix}\n{chunk}",
            )

    async def _push_message(
        self,
        *,
        session_id: str,
        task_id: str,
        status: str,
        text: str,
    ) -> None:
        result = self.push_callback(
            Message(
                text=text,
                sender="hypo-coder",
                session_id=session_id,
                channel="system",
                message_tag="tool_status",
                metadata={"source": "hypo_coder", "task_id": task_id, "status": status},
            )
        )
        if hasattr(result, "__await__"):
            await result
