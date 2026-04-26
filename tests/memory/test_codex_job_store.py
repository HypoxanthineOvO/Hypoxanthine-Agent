from __future__ import annotations

import asyncio
import json

from hypo_agent.memory.structured_store import StructuredStore


def test_structured_store_records_codex_jobs_and_events(tmp_path) -> None:
    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()

        await store.create_codex_job(
            job_id="codex-job-1",
            session_id="s1",
            operation="inspect_repo",
            prompt_summary="inspect repo",
            working_directory="/repo",
            trace_id="trace-1",
            status="running",
            isolation_mode="dedicated_codex_home",
            thread_id="thread-1",
        )
        await store.append_codex_job_event(
            job_id="codex-job-1",
            event_type="agent_message_delta",
            summary="正在检查",
            payload_json=json.dumps({"delta": "正在检查"}, ensure_ascii=False),
        )
        await store.update_codex_job(
            job_id="codex-job-1",
            status="completed",
            result_summary="done",
        )

        row = await store.get_codex_job("codex-job-1")
        events = await store.list_codex_job_events("codex-job-1")

        assert row is not None
        assert row["session_id"] == "s1"
        assert row["operation"] == "inspect_repo"
        assert row["thread_id"] == "thread-1"
        assert row["trace_id"] == "trace-1"
        assert row["status"] == "completed"
        assert row["isolation_mode"] == "dedicated_codex_home"
        assert events[0]["event_type"] == "agent_message_delta"
        assert "正在检查" in events[0]["summary"]

    asyncio.run(_run())
