from __future__ import annotations

import asyncio

from hypo_agent.core.event_queue import EventQueue


def test_event_queue_fifo() -> None:
    async def _run() -> None:
        queue = EventQueue()
        await queue.put(
            {
                "event_type": "reminder_trigger",
                "reminder_id": 1,
                "title": "喝水",
            }
        )
        await queue.put(
            {
                "event_type": "heartbeat_trigger",
                "reminder_id": 2,
                "title": "巡检异常",
            }
        )

        first = await queue.get()
        second = await queue.get()
        queue.task_done()
        queue.task_done()

        assert first["event_type"] == "reminder_trigger"
        assert second["event_type"] == "heartbeat_trigger"

    asyncio.run(_run())


def test_event_queue_helpers() -> None:
    async def _run() -> None:
        queue = EventQueue()
        assert queue.empty() is True
        assert queue.qsize() == 0

        await queue.put({"event_type": "reminder_trigger", "reminder_id": 3})
        assert queue.empty() is False
        assert queue.qsize() == 1

        await queue.get()
        queue.task_done()
        assert queue.empty() is True
        assert queue.qsize() == 0

    asyncio.run(_run())
