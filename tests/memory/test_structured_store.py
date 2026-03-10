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
        )

        sessions = await store.list_sessions()
        pref = await store.get_preference("language")
        usages = await store.list_token_usage("s1")

        assert sessions[0]["session_id"] == "s1"
        assert pref == "zh-CN"
        assert usages[0]["total_tokens"] == 20

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
