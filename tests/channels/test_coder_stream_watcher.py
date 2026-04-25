from __future__ import annotations

import asyncio

from hypo_agent.channels.coder.coder_stream_watcher import CoderStreamWatcher
from hypo_agent.models import Message


class FakeWatcherService:
    def __init__(self) -> None:
        self.status_sequences: dict[str, list[dict[str, object]]] = {}
        self.output_sequences: dict[str, list[dict[str, object]]] = {}
        self.status_calls: list[str] = []
        self.output_calls: list[tuple[str, str | None]] = []
        self.attached_tasks: dict[str, str | None] = {}
        self.incremental_output_supported = False

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

    def supports_incremental_output(self) -> bool:
        return self.incremental_output_supported

    async def get_task_output(
        self,
        *,
        task_id: str,
        after: str | None = None,
    ) -> dict[str, object]:
        self.output_calls.append((task_id, after))
        sequence = self.output_sequences.get(task_id)
        if not sequence:
            return {"cursor": str(after or ""), "lines": [], "done": False}
        if len(sequence) > 1:
            return dict(sequence.pop(0))
        return dict(sequence[0])

def test_watcher_starts_once_and_emits_status_transitions() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "running"},
            {
                "task_id": "task-1",
                "status": "completed",
                "result": {
                    "summary": "已完成检查。",
                    "fileChanges": [],
                    "testsPassed": True,
                },
            },
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
        assert "编码任务完成" in str(pushed[1].text)
        assert "摘要：已完成检查。" in str(pushed[1].text)
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
        assert watcher.is_watching("task-1") is True
        await asyncio.sleep(0.02)
        service.attached_tasks["s1"] = None
        await asyncio.sleep(0.06)

        assert len(pushed) == 1
        assert "RUNNING" in str(pushed[0].text)
        assert watcher.is_watching("task-1") is False

        await watcher.close()

    asyncio.run(_run())


def test_watcher_pushes_incremental_output_and_advances_cursor() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.incremental_output_supported = True
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "running"},
            {
                "task_id": "task-1",
                "status": "completed",
                "result": {"summary": "已完成。", "fileChanges": [], "testsPassed": True},
            },
        ]
        service.output_sequences["task-1"] = [
            {"cursor": "cursor-1", "lines": ["line 1", "line 2"], "done": False},
            {"cursor": "cursor-2", "lines": ["line 3"], "done": False},
            {"cursor": "cursor-2", "lines": [], "done": False},
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
        await asyncio.sleep(0.08)

        assert service.output_calls[:2] == [("task-1", None), ("task-1", "cursor-1")]
        assert len(pushed) == 4
        assert "RUNNING" in str(pushed[0].text)
        assert str(pushed[1].text).startswith("[Codex | task-1]")
        assert "line 1\nline 2" in str(pushed[1].text)
        assert str(pushed[2].text).startswith("[Codex | task-1]")
        assert "line 3" in str(pushed[2].text)
        assert "编码任务完成" in str(pushed[3].text)

        await watcher.close()

    asyncio.run(_run())


def test_watcher_aggregates_incremental_output_within_char_limit() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.incremental_output_supported = True
        service.status_sequences["task-1"] = [{"task_id": "task-1", "status": "running"}]
        service.output_sequences["task-1"] = [
            {
                "cursor": "cursor-1",
                "lines": ["A" * 380, "B" * 380, "C" * 380],
                "done": True,
            }
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
        await asyncio.sleep(0.05)

        codex_messages = [message for message in pushed if str(message.text).startswith("[Codex | ")]
        assert len(codex_messages) == 2
        assert all(len(str(message.text)) <= 800 for message in codex_messages)
        assert "A" * 100 in str(codex_messages[0].text)
        assert "C" * 100 in str(codex_messages[1].text)

        await watcher.close()

    asyncio.run(_run())


def test_watcher_skips_incremental_output_when_backend_does_not_support_it() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.incremental_output_supported = False
        service.status_sequences["task-1"] = [
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
        await asyncio.sleep(0.05)

        assert service.output_calls == []
        assert len(pushed) == 2
        assert "RUNNING" in str(pushed[0].text)
        assert "编码任务完成" in str(pushed[1].text)

        await watcher.close()

    asyncio.run(_run())


def test_watcher_stops_when_incremental_output_marks_done() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.incremental_output_supported = True
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "running"},
        ]
        service.output_sequences["task-1"] = [
            {"cursor": "cursor-1", "lines": ["still working"], "done": True}
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
        await asyncio.sleep(0.05)

        assert watcher.is_watching("task-1") is False
        assert service.status_calls == ["task-1"]
        assert service.output_calls == [("task-1", None)]
        assert len(pushed) == 2
        assert "RUNNING" in str(pushed[0].text)
        assert str(pushed[1].text).startswith("[Codex | task-1]")

        await watcher.close()

    asyncio.run(_run())


def test_watcher_starts_from_initial_cursor_without_replaying_history() -> None:
    async def _run() -> None:
        pushed: list[Message] = []
        service = FakeWatcherService()
        service.incremental_output_supported = True
        service.status_sequences["task-1"] = [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "completed"},
        ]
        service.output_sequences["task-1"] = [
            {"cursor": "cursor-3", "lines": ["new line"], "done": False},
            {"cursor": "cursor-3", "lines": [], "done": False},
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

        await watcher.start(task_id="task-1", session_id="s1", initial_cursor="cursor-2")
        await asyncio.sleep(0.05)

        assert service.output_calls[0] == ("task-1", "cursor-2")
        assert any("new line" in str(message.text) for message in pushed)

        await watcher.close()

    asyncio.run(_run())
