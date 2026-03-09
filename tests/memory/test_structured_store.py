from __future__ import annotations

import asyncio

import aiosqlite

from hypo_agent.memory.structured_store import StructuredStore


def test_structured_store_sessions_preferences_and_token_usage(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.upsert_session("s1")
        await store.set_preference("language", "zh-CN")
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
            latency_ms=123.4,
        )

        sessions = await store.list_sessions()
        pref = await store.get_preference("language")
        usages = await store.list_token_usage("s1")

        assert sessions[0]["session_id"] == "s1"
        assert pref == "zh-CN"
        assert usages[0]["total_tokens"] == 20
        assert usages[0]["latency_ms"] == 123.4

    asyncio.run(_run())


def test_structured_store_persists_across_instances(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _seed() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.upsert_session("persisted-session")
        await store.set_preference("timezone", "UTC")

    async def _verify() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        sessions = await store.list_sessions()
        timezone = await store.get_preference("timezone")
        assert sessions[0]["session_id"] == "persisted-session"
        assert timezone == "UTC"

    asyncio.run(_seed())
    asyncio.run(_verify())


def test_structured_store_summarizes_token_and_latency_by_model(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=100.0,
        )
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
            latency_ms=200.0,
        )
        await store.record_token_usage(
            session_id="s2",
            requested_model="DeepseekV3_2",
            resolved_model="DeepseekV3_2",
            input_tokens=7,
            output_tokens=4,
            total_tokens=11,
            latency_ms=50.0,
        )

        token_summary = await store.summarize_token_usage()
        latency_summary = await store.summarize_latency_by_model()
        session_summary = await store.summarize_token_usage(session_id="s1")

        assert token_summary["totals"]["input_tokens"] == 20
        assert token_summary["totals"]["output_tokens"] == 11
        assert token_summary["totals"]["total_tokens"] == 31

        by_model = {row["resolved_model"]: row for row in token_summary["rows"]}
        assert by_model["Gemini3Pro"]["total_tokens"] == 20
        assert by_model["Gemini3Pro"]["calls"] == 2
        assert by_model["DeepseekV3_2"]["total_tokens"] == 11
        assert by_model["DeepseekV3_2"]["calls"] == 1

        latency_by_model = {row["resolved_model"]: row for row in latency_summary}
        assert latency_by_model["Gemini3Pro"]["calls"] == 2
        assert latency_by_model["Gemini3Pro"]["min_latency_ms"] == 100.0
        assert latency_by_model["Gemini3Pro"]["max_latency_ms"] == 200.0
        assert latency_by_model["Gemini3Pro"]["avg_latency_ms"] == 150.0

        assert session_summary["totals"]["total_tokens"] == 20
        assert len(session_summary["rows"]) == 1
        assert session_summary["rows"][0]["resolved_model"] == "Gemini3Pro"

    asyncio.run(_run())


def test_structured_store_records_tool_invocations(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        invocation_id = await store.record_tool_invocation(
            session_id="s1",
            tool_name="run_command",
            skill_name="tmux",
            params_json='{"command":"echo hi"}',
            status="success",
            result_summary="ok",
            duration_ms=12.5,
            error_info="",
            compressed_meta_json='{"cache_id":"abc","original_chars":1000,"compressed_chars":120}',
        )

        rows = await store.list_tool_invocations(session_id="s1")
        assert len(rows) == 1
        row = rows[0]
        assert invocation_id == row["id"]
        assert row["session_id"] == "s1"
        assert row["tool_name"] == "run_command"
        assert row["skill_name"] == "tmux"
        assert row["params_json"] == '{"command":"echo hi"}'
        assert row["status"] == "success"
        assert row["result_summary"] == "ok"
        assert row["duration_ms"] == 12.5
        assert row["compressed_meta_json"] == (
            '{"cache_id":"abc","original_chars":1000,"compressed_chars":120}'
        )

    asyncio.run(_run())


def test_structured_store_updates_tool_invocation_compressed_meta(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        invocation_id = await store.record_tool_invocation(
            session_id="s1",
            tool_name="run_command",
            skill_name="tmux",
            params_json='{"command":"echo hi"}',
            status="success",
            result_summary="ok",
            duration_ms=1.0,
            error_info="",
        )
        assert invocation_id is not None
        await store.update_tool_invocation_compressed_meta(
            invocation_id,
            compressed_meta_json='{"cache_id":"cache_1","original_chars":5000,"compressed_chars":120}',
        )

        rows = await store.list_tool_invocations(session_id="s1")
        assert len(rows) == 1
        assert rows[0]["compressed_meta_json"] == (
            '{"cache_id":"cache_1","original_chars":5000,"compressed_chars":120}'
        )

    asyncio.run(_run())


def test_structured_store_list_token_usage_supports_since_filter(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=100.0,
        )
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            latency_ms=110.0,
        )

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE token_usage SET created_at = ? WHERE id = (SELECT MIN(id) FROM token_usage)",
                ("2026-03-01T00:00:00+00:00",),
            )
            await db.execute(
                "UPDATE token_usage SET created_at = ? WHERE id = (SELECT MAX(id) FROM token_usage)",
                ("2026-03-06T00:00:00+00:00",),
            )
            await db.commit()

        rows = await store.list_token_usage(
            session_id="s1",
            since_iso="2026-03-05T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["total_tokens"] == 30

    asyncio.run(_run())


def test_structured_store_delete_session_data_cleans_all_related_rows(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
            latency_ms=100.0,
        )
        await store.record_tool_invocation(
            session_id="s1",
            tool_name="run_command",
            skill_name="tmux",
            params_json='{"command":"echo hi"}',
            status="success",
            result_summary="ok",
            duration_ms=12.5,
            error_info="",
        )

        await store.delete_session_data("s1")

        assert await store.list_token_usage("s1") == []
        assert await store.list_tool_invocations(session_id="s1") == []
        assert all(item["session_id"] != "s1" for item in await store.list_sessions())

    asyncio.run(_run())


def test_structured_store_reminders_crud(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        reminder_id = await store.create_reminder(
            title="喝水",
            description="每小时喝水",
            schedule_type="cron",
            schedule_value="0 * * * *",
            channel="all",
            status="active",
            next_run_at="2026-03-07T08:00:00+00:00",
            heartbeat_config='[{"check_type":"file_exists","target":"/tmp/ok"}]',
        )

        inserted = await store.get_reminder(reminder_id)
        assert inserted is not None
        assert inserted["id"] == reminder_id
        assert inserted["title"] == "喝水"
        assert inserted["schedule_type"] == "cron"

        await store.update_reminder(
            reminder_id,
            title="起身活动",
            description="每小时起身",
            schedule_type="once",
            schedule_value="2026-03-07T09:00:00+00:00",
            channel="all",
            status="paused",
            next_run_at="2026-03-07T09:00:00+00:00",
            heartbeat_config="[]",
        )
        updated = await store.get_reminder(reminder_id)
        assert updated is not None
        assert updated["title"] == "起身活动"
        assert updated["status"] == "paused"

        await store.set_reminder_next_run_at(
            reminder_id,
            "2026-03-07T10:00:00+00:00",
        )
        rerun = await store.get_reminder(reminder_id)
        assert rerun is not None
        assert rerun["next_run_at"] == "2026-03-07T10:00:00+00:00"

        await store.mark_reminder_completed(reminder_id)
        done = await store.get_reminder(reminder_id)
        assert done is not None
        assert done["status"] == "completed"

        await store.delete_reminder(reminder_id)
        deleted = await store.get_reminder(reminder_id)
        assert deleted is not None
        assert deleted["status"] == "deleted"

    asyncio.run(_run())


def test_structured_store_list_reminders_filters_status(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.create_reminder(
            title="A",
            description="",
            schedule_type="once",
            schedule_value="2026-03-07T08:00:00+00:00",
            channel="all",
            status="active",
            next_run_at="2026-03-07T08:00:00+00:00",
            heartbeat_config=None,
        )
        await store.create_reminder(
            title="B",
            description="",
            schedule_type="once",
            schedule_value="2026-03-07T09:00:00+00:00",
            channel="all",
            status="paused",
            next_run_at="2026-03-07T09:00:00+00:00",
            heartbeat_config=None,
        )

        active = await store.list_reminders(status="active")
        paused = await store.list_reminders(status="paused")
        all_rows = await store.list_reminders(status=None)

        assert len(active) == 1
        assert active[0]["title"] == "A"
        assert len(paused) == 1
        assert paused[0]["title"] == "B"
        assert len(all_rows) == 2

    asyncio.run(_run())
