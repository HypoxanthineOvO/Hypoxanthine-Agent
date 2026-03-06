from __future__ import annotations

import asyncio

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
