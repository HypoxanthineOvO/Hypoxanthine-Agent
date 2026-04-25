from __future__ import annotations

import asyncio
import json
import sqlite3

from hypo_agent.memory.structured_store import StructuredStore


def test_structured_store_records_and_queries_repair_runs(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()

        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="Genesis QWen 工具调用后误报无法访问",
            finding_id="F1",
            working_directory="/home/heyx/Hypo-Agent",
            status="running",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json=json.dumps({"hours": 24}, ensure_ascii=False),
            verify_commands_json=json.dumps(["pytest tests/core/test_pipeline_tools.py"], ensure_ascii=False),
            codex_thread_id="thread-1",
            git_status_before=" M src/hypo_agent/core/pipeline.py",
        )
        await store.create_repair_run(
            run_id="repair-2",
            session_id="s1",
            issue_text="retry previous repair",
            working_directory="/home/heyx/Hypo-Agent",
            status="queued",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
            retry_of_run_id="repair-1",
        )

        row = await store.get_repair_run("repair-1")
        latest = await store.get_latest_repair_run_for_session("s1")
        active = await store.get_active_repair_run()
        rows = await store.list_repair_runs(session_id="s1")

        assert row is not None
        assert row["run_id"] == "repair-1"
        assert row["finding_id"] == "F1"
        assert row["codex_thread_id"] == "thread-1"
        assert row["issue_text"] == "Genesis QWen 工具调用后误报无法访问"
        assert row["git_status_before"] == " M src/hypo_agent/core/pipeline.py"

        assert latest is not None
        assert latest["run_id"] == "repair-2"
        assert latest["retry_of_run_id"] == "repair-1"

        assert active is not None
        assert active["run_id"] == "repair-2"

        assert [item["run_id"] for item in rows] == ["repair-2", "repair-1"]

    asyncio.run(_run())


def test_structured_store_updates_repair_run_and_events(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()

        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="fix repair flow",
            working_directory="/home/heyx/Hypo-Agent",
            status="queued",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
            codex_thread_id="thread-2",
        )

        await store.update_repair_run(
            "repair-1",
            codex_thread_id="thread-123",
            status="completed",
            verification_state="passed",
            restart_state="executed",
            git_status_after=" M src/hypo_agent/core/repair_service.py",
            report_markdown="## Repair Report",
            report_json=json.dumps({"needs_restart": True}, ensure_ascii=False),
            last_error="",
        )
        await store.append_repair_run_event(
            run_id="repair-1",
            event_type="task.completed",
            source="webhook",
            summary="Repair finished",
            payload_json=json.dumps({"task_id": "task-123"}, ensure_ascii=False),
        )

        row = await store.get_repair_run("repair-1")
        by_task = await store.get_repair_run_by_thread_id("thread-123")
        events = await store.list_repair_run_events("repair-1")

        assert row is not None
        assert row["codex_thread_id"] == "thread-123"
        assert row["status"] == "completed"
        assert row["verification_state"] == "passed"
        assert row["restart_state"] == "executed"
        assert row["git_status_after"] == " M src/hypo_agent/core/repair_service.py"
        assert row["report_markdown"] == "## Repair Report"

        assert by_task is not None
        assert by_task["run_id"] == "repair-1"

        assert len(events) == 1
        assert events[0]["event_type"] == "task.completed"
        assert events[0]["source"] == "webhook"
        assert events[0]["summary"] == "Repair finished"

    asyncio.run(_run())


def test_structured_store_migrates_legacy_repair_runs_without_codex_thread_id(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            """
            CREATE TABLE repair_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                coder_task_id TEXT,
                retry_of_run_id TEXT,
                issue_text TEXT NOT NULL,
                finding_id TEXT,
                working_directory TEXT NOT NULL,
                status TEXT NOT NULL,
                verification_state TEXT NOT NULL DEFAULT 'pending',
                restart_state TEXT NOT NULL DEFAULT 'not_requested',
                diagnostic_snapshot_json TEXT NOT NULL,
                verify_commands_json TEXT NOT NULL DEFAULT '[]',
                git_status_before TEXT NOT NULL DEFAULT '',
                git_status_after TEXT NOT NULL DEFAULT '',
                report_markdown TEXT NOT NULL DEFAULT '',
                report_json TEXT NOT NULL DEFAULT '{}',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        db.commit()
    finally:
        db.close()

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()

        conn = sqlite3.connect(db_path)
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(repair_runs)")
            }
            index_names = {
                str(row[1])
                for row in conn.execute("PRAGMA index_list(repair_runs)")
            }
        finally:
            conn.close()

        assert "codex_thread_id" in columns
        assert "idx_repair_runs_thread" in index_names

    asyncio.run(_run())
