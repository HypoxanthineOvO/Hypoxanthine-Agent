from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from hypo_agent.memory.consolidation import MemoryConsolidationService
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


def _append_old_messages(session_memory: SessionMemory, session_id: str, lines: list[str]) -> None:
    for index, text in enumerate(lines):
        session_memory.append(
            Message(
                text=text,
                sender="user" if index % 2 == 0 else "assistant",
                session_id=session_id,
                timestamp=datetime(2026, 3, 1, 9, index, tzinfo=UTC),
            )
        )


def test_consolidation_extracts_session_candidates_deduplicates_and_reports(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
        _append_old_messages(
            session_memory,
            "old-session",
            [
                "记忆: user_profile.favorite_drink = 绿茶",
                "记忆: user_profile.favorite_drink = 绿茶",
                "记忆: interaction_policy.reply_boundary = 答完直接结束，不要追加反问",
                "普通聊天内容",
                "继续聊天让会话达到 GC 阈值",
            ],
        )

        service = MemoryConsolidationService(
            session_memory=session_memory,
            structured_store=store,
            knowledge_dir=knowledge_dir,
            sessions_dir=sessions_dir,
            backup_dir=tmp_path / "backups",
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        report = await service.run(apply=True)

        assert report["counts"]["added"] == 2
        assert report["counts"]["skipped"] == 1
        assert Path(report["backup_manifest"]["manifest_path"]).exists()
        assert Path(report["report_file"]).exists()
        assert any(
            item["key"] == "favorite_drink"
            and item["action"] == "skipped"
            and item["reason"] == "duplicate_candidate"
            for item in report["items"]
        )
        saved = await store.list_memory_items()
        by_key = {item["key"]: item for item in saved}
        assert by_key["favorite_drink"]["memory_class"] == "user_profile"
        assert by_key["favorite_drink"]["value"] == "绿茶"
        assert by_key["reply_boundary"]["memory_class"] == "interaction_policy"

    asyncio.run(_run())


def test_consolidation_marks_conflicts_without_overwriting_existing_memory(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        await store.save_memory_item(
            memory_class="user_profile",
            key="favorite_drink",
            value="红茶",
            source="manual",
            language="zh",
        )
        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
        _append_old_messages(
            session_memory,
            "old-session",
            [
                "记忆: user_profile.favorite_drink = 绿茶",
                "闲聊 1",
                "闲聊 2",
                "闲聊 3",
                "闲聊 4",
            ],
        )

        service = MemoryConsolidationService(
            session_memory=session_memory,
            structured_store=store,
            knowledge_dir=knowledge_dir,
            sessions_dir=sessions_dir,
            backup_dir=tmp_path / "backups",
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        report = await service.run(apply=True)

        assert report["counts"]["conflicts"] == 1
        assert report["counts"]["added"] == 0
        [item] = [item for item in report["items"] if item["key"] == "favorite_drink"]
        assert item["action"] == "conflict"
        assert item["reason"] == "existing_value_conflict"
        saved = await store.list_memory_items(memory_class="user_profile")
        assert saved[0]["value"] == "红茶"

    asyncio.run(_run())


def test_consolidation_archives_existing_memory_from_archive_candidate(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        await store.save_memory_item(
            memory_class="user_profile",
            key="favorite_drink",
            value="绿茶",
            source="memory_consolidation:session:old-session",
            language="zh",
        )
        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
        _append_old_messages(
            session_memory,
            "old-session",
            [
                "记忆归档: user_profile.favorite_drink = 用户撤回该偏好",
                "闲聊 1",
                "闲聊 2",
                "闲聊 3",
                "闲聊 4",
            ],
        )

        service = MemoryConsolidationService(
            session_memory=session_memory,
            structured_store=store,
            knowledge_dir=knowledge_dir,
            sessions_dir=sessions_dir,
            backup_dir=tmp_path / "backups",
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        report = await service.run(apply=True)

        assert report["counts"]["archived"] == 1
        assert report["items"][0]["action"] == "archived"
        assert report["items"][0]["reason"] == "archive_candidate_applied"
        assert await store.list_memory_items(memory_class="user_profile") == []
        archived = await store.list_memory_items(memory_class="user_profile", status="archived")
        assert archived[0]["key"] == "favorite_drink"
        assert archived[0]["value"] == "绿茶"

    asyncio.run(_run())


def test_consolidation_imports_legacy_preferences_and_rolls_back_from_report(tmp_path: Path) -> None:
    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        await store.set_preference("reply_boundary", "答完直接结束，不要追加反问")
        session_memory = SessionMemory(sessions_dir=tmp_path / "memory" / "sessions")
        service = MemoryConsolidationService(
            session_memory=session_memory,
            structured_store=store,
            knowledge_dir=tmp_path / "memory" / "knowledge",
            backup_dir=tmp_path / "backups",
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        report = await service.run(apply=True)

        assert report["counts"]["added"] == 1
        assert report["items"][0]["reason"] == "legacy_preference_imported"
        assert await store.list_memory_items(memory_class="interaction_policy") != []

        await service.rollback(report["report_file"])

        assert await store.list_memory_items() == []
        assert await store.get_preference("reply_boundary") == "答完直接结束，不要追加反问"

    asyncio.run(_run())
