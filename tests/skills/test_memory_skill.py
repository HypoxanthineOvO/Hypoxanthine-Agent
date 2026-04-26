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
        assert output.result["storage_path"] == str(db_path)
        assert output.result["storage_folder"] == str(db_path.parent)
        assert str(db_path) in output.result["human_summary"]
        assert str(db_path.parent) in output.result["human_summary"]
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


def test_memory_skill_saves_and_lists_typed_memory_items(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        skill = MemorySkill(structured_store=store)

        saved = await skill.execute(
            "save_memory_item",
            {
                "memory_class": "interaction_policy",
                "key": "reply_boundary",
                "value": "答完直接结束，不要追加反问",
            },
        )
        listed = await skill.execute(
            "list_memory_items",
            {"memory_class": "interaction_policy"},
        )

        assert saved.status == "success"
        assert saved.result["memory_class"] == "interaction_policy"
        assert listed.status == "success"
        assert listed.result["items"][0]["key"] == "reply_boundary"
        assert listed.result["items"][0]["language"] == "zh"

    asyncio.run(_run())
