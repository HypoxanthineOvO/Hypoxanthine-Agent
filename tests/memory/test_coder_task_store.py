from __future__ import annotations

import asyncio

from hypo_agent.memory.structured_store import StructuredStore


def test_structured_store_records_and_queries_coder_tasks(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()

        await store.create_coder_task(
            task_id="task-1",
            session_id="s1",
            working_directory="/repo/one",
            prompt_summary="fix login bug",
            model="o4-mini",
            status="running",
            attached=True,
        )
        await store.create_coder_task(
            task_id="task-2",
            session_id="s1",
            working_directory="/repo/two",
            prompt_summary="add tests",
            model="o4-mini",
            status="queued",
            attached=False,
        )

        row = await store.get_coder_task("task-1")
        latest = await store.get_latest_coder_task_for_session("s1")
        attached = await store.get_attached_coder_task_for_session("s1")
        rows = await store.list_coder_tasks(session_id="s1")

        assert row is not None
        assert row["task_id"] == "task-1"
        assert row["session_id"] == "s1"
        assert row["working_directory"] == "/repo/one"
        assert row["prompt_summary"] == "fix login bug"
        assert row["model"] == "o4-mini"
        assert row["status"] == "running"
        assert row["attached"] == 1

        assert latest is not None
        assert latest["task_id"] == "task-2"

        assert attached is not None
        assert attached["task_id"] == "task-1"

        assert [item["task_id"] for item in rows] == ["task-2", "task-1"]

    asyncio.run(_run())


def test_structured_store_updates_attach_status_done_and_status(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()

        await store.create_coder_task(
            task_id="task-1",
            session_id="s1",
            working_directory="/repo/one",
            prompt_summary="fix login bug",
            model="o4-mini",
            status="running",
            attached=True,
        )
        await store.create_coder_task(
            task_id="task-2",
            session_id="s1",
            working_directory="/repo/two",
            prompt_summary="add tests",
            model="o4-mini",
            status="queued",
            attached=False,
        )

        await store.attach_coder_task(session_id="s1", task_id="task-2")
        attached = await store.get_attached_coder_task_for_session("s1")
        assert attached is not None
        assert attached["task_id"] == "task-2"

        await store.detach_coder_task(session_id="s1")
        assert await store.get_attached_coder_task_for_session("s1") is None

        await store.attach_coder_task(session_id="s1", task_id="task-1")
        await store.mark_coder_task_done(session_id="s1")
        done_row = await store.get_coder_task("task-1")
        assert done_row is not None
        assert done_row["done"] == 1
        assert done_row["attached"] == 0
        assert await store.get_attached_coder_task_for_session("s1") is None

        await store.update_coder_task_status(
            task_id="task-2",
            status="completed",
            last_error="",
        )
        updated = await store.get_coder_task("task-2")
        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["last_error"] == ""

    asyncio.run(_run())
