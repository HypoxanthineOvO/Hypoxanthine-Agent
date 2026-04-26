from __future__ import annotations

import asyncio

from hypo_agent.channels.codex_bridge import CodexThread
from hypo_agent.core.codex_job_service import CodexJobService
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class FakeCodexBridge:
    isolation_mode = "dedicated_codex_home"

    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.abort_calls: list[str] = []
        self._on_complete = None
        self._on_event = None

    async def submit(self, *, run_id, prompt, working_dir, on_complete, on_event=None):
        self.submit_calls.append({"run_id": run_id, "prompt": prompt, "working_dir": working_dir})
        self._on_complete = on_complete
        self._on_event = on_event
        return CodexThread(
            thread_id="thread-1",
            run_id=run_id,
            working_dir=working_dir,
            status="running",
        )

    async def abort(self, run_id: str) -> None:
        self.abort_calls.append(run_id)

    async def emit_event(self, run_id: str, event_type: str, payload: dict) -> None:
        assert self._on_event is not None
        await self._on_event(run_id, event_type, payload)

    async def complete(self, run_id: str, status: str, result: str | None) -> None:
        assert self._on_complete is not None
        await self._on_complete(run_id, status, result)


def test_codex_job_service_persists_job_and_keeps_progress_out_of_l1(tmp_path) -> None:
    async def _run() -> None:
        pushed = []

        async def push(message) -> None:
            pushed.append(message)

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions")
        bridge = FakeCodexBridge()
        service = CodexJobService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            proactive_callback=push,
            default_working_directory="/repo",
        )

        payload = await service.submit_job(
            session_id="s1",
            operation="inspect_repo",
            prompt="inspect repository",
        )
        job_id = str(payload["job_id"])
        await bridge.emit_event(job_id, "agent_message_delta", {"delta": "raw codex transcript"})
        await bridge.complete(job_id, "completed", "final summary")

        row = await store.get_codex_job(job_id)
        events = await store.list_codex_job_events(job_id)

        assert row is not None
        assert row["thread_id"] == "thread-1"
        assert row["status"] == "completed"
        assert row["isolation_mode"] == "dedicated_codex_home"
        assert row["trace_id"].startswith("trace-")
        assert events[0]["event_type"] == "agent_message_delta"
        assert pushed
        assert pushed[0].metadata["transient"] is True
        assert pushed[0].metadata["persist_to_l1"] is False
        assert memory.get_messages("s1") == []

    asyncio.run(_run())


def test_codex_job_service_abort_sets_terminal_status(tmp_path) -> None:
    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        bridge = FakeCodexBridge()
        service = CodexJobService(
            structured_store=store,
            session_memory=None,
            codex_bridge=bridge,
            default_working_directory="/repo",
        )

        payload = await service.submit_job(
            session_id="s1",
            operation="run_verification",
            prompt="run tests",
        )
        job_id = str(payload["job_id"])
        await service.abort_job(job_id)
        row = await store.get_codex_job(job_id)

        assert bridge.abort_calls == [job_id]
        assert row is not None
        assert row["status"] == "aborted"
        assert row["last_error"] == "aborted by user"

    asyncio.run(_run())
