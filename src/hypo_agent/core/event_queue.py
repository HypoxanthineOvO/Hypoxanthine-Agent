from __future__ import annotations

import asyncio
from typing import Any, Literal

SchedulerEventType = Literal[
    "reminder_trigger",
    "heartbeat_trigger",
    "email_scan_trigger",
    "hypo_info_trigger",
    "wewe_rss_trigger",
]


class EventQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def put(self, event: dict[str, Any]) -> None:
        await self._queue.put(event)

    async def get(self) -> dict[str, Any]:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()
