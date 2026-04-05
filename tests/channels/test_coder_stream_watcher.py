from __future__ import annotations

import asyncio

from hypo_agent.channels.coder.coder_stream_watcher import CoderStreamWatcher
from hypo_agent.models import Message


class FakeWatcherService:
    def __init__(self) -> None:
        self.status_sequences: dict[str, list[dict[str, object]]] = {}
        self.status_calls: list[str] = []
        self.attached_tasks: dict[str, str | None] = {}

    async def get_task_status(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> dict[str, object]:
        del session_id
        self.status_calls.append(task_id)
        sequence = self.status_sequences[task_id]
        if len(sequence) > 1:
            return dict(sequence.pop(0))
        return dict(sequence[0])

    async def get_attached_task(self, session_id: str) -> dict[str, object] | None:
        task_id = self.attached_tasks.get(session_id)
        if not task_id:
            return None
        return {"task_id": task_id}


def test_watcher_starts_once_and_emits_status_transitions() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "completed"},
        ]
        service.attached_tasks["s1"] = "task-1"

        async def capture(message: Message) -> None:
            pushed.append(message)

        watcher = CoderStreamWatcher(
            coder_task_service=service,
            push_callback=capture,
            poll_interval_seconds=0.01,
            message_char_limit=800,
        )

        first = await watcher.start(task_id="task-1", session_id="s1")
        second = await watcher.start(task_id="task-1", session_id="s1")
        await asyncio.sleep(0.08)

        assert first is True
        assert second is False
        assert len(pushed) == 2
        assert "RUNNING" in str(pushed[0].text)
        assert "COMPLETED" in str(pushed[1].text)
        assert "task-1" in str(pushed[0].text)

        await watcher.close()

    asyncio.run(_run())


def test_watcher_stops_emitting_after_detach() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "completed"},
        ]
        service.attached_tasks["s1"] = "task-1"

        async def capture(message: Message) -> None:
            pushed.append(message)

        watcher = CoderStreamWatcher(
            coder_task_service=service,
            push_callback=capture,
            poll_interval_seconds=0.01,
            message_char_limit=800,
        )

        await watcher.start(task_id="task-1", session_id="s1")
        await asyncio.sleep(0.02)
        service.attached_tasks["s1"] = None
        await asyncio.sleep(0.06)

        assert len(pushed) == 1
        assert "RUNNING" in str(pushed[0].text)

        await watcher.close()

    asyncio.run(_run())
