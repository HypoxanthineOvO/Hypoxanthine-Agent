from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from hypo_agent.models import Message

_TERMINAL_STATES = {"completed", "failed", "aborted"}


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

    async def start(self, *, task_id: str, session_id: str) -> bool:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return False
        existing = self._tasks.get(normalized_task_id)
        if existing is not None and not existing.done():
            return False
        self._tasks[normalized_task_id] = asyncio.create_task(
            self._watch(task_id=normalized_task_id, session_id=session_id)
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

    async def _watch(self, *, task_id: str, session_id: str) -> None:
        last_status = ""
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
                    await self._emit_status(session_id=session_id, task_id=task_id, status=status)
                    last_status = status

                if status in _TERMINAL_STATES:
                    break
                await asyncio.sleep(self.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            self._tasks.pop(task_id, None)

    async def _emit_status(self, *, session_id: str, task_id: str, status: str) -> None:
        text = "\n".join(
            [
                "─────────────────────────────────",
                f"🤖 Codex · {task_id} | {status.upper()}",
                "─────────────────────────────────",
            ]
        )
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
