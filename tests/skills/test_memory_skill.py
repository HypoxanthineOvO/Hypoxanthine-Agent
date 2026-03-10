from __future__ import annotations

import asyncio

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.memory_skill import MemorySkill


def test_save_preference_tool(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        skill = MemorySkill(structured_store=store)
        output = await skill.execute(
            "save_preference",
            {"key": "favorite_drink", "value": "绿茶"},
        )
        assert output.status == "success"
        assert await store.get_preference("favorite_drink") == "绿茶"

    asyncio.run(_run())


def test_get_preference_tool(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.set_preference("timezone", "UTC")
        skill = MemorySkill(structured_store=store)
        output = await skill.execute("get_preference", {"key": "timezone"})
        assert output.status == "success"
        assert output.result["value"] == "UTC"

    asyncio.run(_run())


def test_preference_upsert(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        skill = MemorySkill(structured_store=store)
        await skill.execute("save_preference", {"key": "language", "value": "zh-CN"})
        await skill.execute("save_preference", {"key": "language", "value": "en-US"})
        assert await store.get_preference("language") == "en-US"

    asyncio.run(_run())

