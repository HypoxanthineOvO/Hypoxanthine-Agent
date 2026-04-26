from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.memory.typed_memory import TypedMemoryMigrator, classify_legacy_memory_key


def test_classify_legacy_memory_keys() -> None:
    assert classify_legacy_memory_key("reply_boundary", "答完直接结束") == "interaction_policy"
    assert classify_legacy_memory_key("favorite_drink", "绿茶") == "user_profile"
    assert classify_legacy_memory_key("auth.pending.zhihu", "{}") == "credentials_state"
    assert classify_legacy_memory_key("email_scan.cursor", "abc") == "operational_state"
    assert classify_legacy_memory_key("notion.todo_database_id", "db") == "operational_state"


def test_typed_memory_migration_backup_prompt_filter_and_rollback(tmp_path: Path) -> None:
    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        await store.set_preference("reply_boundary", "答完直接结束，不要追加反问")
        await store.set_preference("favorite_drink", "绿茶")
        await store.set_preference("auth.pending.zhihu", '{"state":"waiting"}')
        await store.set_preference("email_scan.cursor", "cursor-1")

        migrator = TypedMemoryMigrator(store, backup_dir=tmp_path / "backups")
        manifest = await migrator.backup(reason="test migration")
        assert Path(manifest["manifest_path"]).exists()
        assert Path(manifest["database_backup_path"]).exists()

        result = await migrator.migrate_legacy_preferences()
        assert result["migrated"] == 4

        items = await store.list_memory_items()
        classes = {item["key"]: item["memory_class"] for item in items}
        assert classes["reply_boundary"] == "interaction_policy"
        assert classes["favorite_drink"] == "user_profile"
        assert classes["auth.pending.zhihu"] == "credentials_state"
        assert classes["email_scan.cursor"] == "operational_state"

        prompt_rows = store.list_prompt_memory_sync(limit=20)
        prompt_text = "\n".join(f"{key}: {value}" for key, value in prompt_rows)
        assert "reply_boundary" in prompt_text
        assert "favorite_drink" in prompt_text
        assert "auth.pending.zhihu" not in prompt_text
        assert "email_scan.cursor" not in prompt_text

        await store.delete_preference("reply_boundary")
        await store.save_memory_item(
            memory_class="user_profile",
            key="temporary",
            value="会被回滚",
            source="test",
            language="zh",
        )
        await migrator.rollback(manifest["manifest_path"])

        assert await store.get_preference("reply_boundary") == "答完直接结束，不要追加反问"
        assert await store.list_memory_items() == []

    asyncio.run(_run())
