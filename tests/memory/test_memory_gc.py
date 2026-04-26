from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from hypo_agent.memory.memory_gc import MemoryGC
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class StubSemanticMemory:
    def __init__(self) -> None:
        self.updated: list[str] = []
        self.rebuilt: list[str] = []

    async def update_index(self, file_path) -> None:
        self.updated.append(str(file_path))

    async def build_index(self, knowledge_dir) -> None:
        self.rebuilt.append(str(knowledge_dir))


class StubModelRouter:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls: list[dict] = []

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "LightweightModel"

    async def call(self, model_name: str, messages, *, session_id=None, tools=None) -> str:
        del tools
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "session_id": session_id,
            }
        )
        return self.text


def test_memory_gc_summarizes_inactive_session_to_l3_and_marks_processed(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        await store.upsert_session("old-session")

        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
        for index in range(5):
            session_memory.append(
                Message(
                    text=f"消息 {index}",
                    sender="user" if index % 2 == 0 else "assistant",
                    session_id="old-session",
                    timestamp=datetime(2026, 3, 1, 9, index, tzinfo=UTC),
                )
            )

        semantic_memory = StubSemanticMemory()
        router = StubModelRouter(
            text=(
                "# 会话摘要\n\n"
                "## 关键决策\n\n- 采用 sqlite-vec 作为向量检索后端。\n"
            )
        )
        gc = MemoryGC(
            session_memory=session_memory,
            structured_store=store,
            semantic_memory=semantic_memory,
            model_router=router,
            knowledge_dir=knowledge_dir,
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        result = await gc.run()

        summary_files = sorted((knowledge_dir / "gc_summaries").glob("*.md"))
        assert result["processed_count"] == 1
        assert len(summary_files) == 1
        assert "sqlite-vec" in summary_files[0].read_text(encoding="utf-8")
        assert semantic_memory.updated == [str(summary_files[0])]
        assert semantic_memory.rebuilt == [str(knowledge_dir)]
        assert await store.is_session_gc_processed("old-session") is True

    asyncio.run(_run())


def test_memory_gc_redacts_sensitive_session_text_before_llm_summary(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        await store.upsert_session("secret-session")
        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
        for index, text in enumerate(
            [
                "auth token=SECRET_TOKEN_SHOULD_NOT_LEAK",
                "cookie=SECRET_COOKIE_SHOULD_NOT_LEAK",
                "用户偏好：回复使用中文",
                "一次普通确认",
                "另一次普通确认",
            ]
        ):
            session_memory.append(
                Message(
                    text=text,
                    sender="user",
                    session_id="secret-session",
                    timestamp=datetime(2026, 3, 1, 9, index, tzinfo=UTC),
                )
            )

        router = StubModelRouter(text="# 会话摘要\n\n- 用户偏好中文。")
        gc = MemoryGC(
            session_memory=session_memory,
            structured_store=store,
            semantic_memory=StubSemanticMemory(),
            model_router=router,
            knowledge_dir=knowledge_dir,
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        await gc.run()

        prompt = router.calls[0]["messages"][0]["content"]
        assert "SECRET_TOKEN_SHOULD_NOT_LEAK" not in prompt
        assert "SECRET_COOKIE_SHOULD_NOT_LEAK" not in prompt
        assert "[已移除敏感凭据内容]" in prompt

    asyncio.run(_run())


def test_memory_gc_skips_recent_short_and_processed_sessions(tmp_path: Path) -> None:
    async def _run() -> None:
        sessions_dir = tmp_path / "memory" / "sessions"
        knowledge_dir = tmp_path / "memory" / "knowledge"
        store = StructuredStore(db_path=tmp_path / "memory" / "hypo.db")
        await store.init()
        session_memory = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)

        await store.upsert_session("short-session")
        for index in range(4):
            session_memory.append(
                Message(
                    text=f"短会话 {index}",
                    sender="user",
                    session_id="short-session",
                    timestamp=datetime(2026, 3, 1, 9, index, tzinfo=UTC),
                )
            )

        await store.upsert_session("recent-session")
        for index in range(5):
            session_memory.append(
                Message(
                    text=f"近期会话 {index}",
                    sender="user",
                    session_id="recent-session",
                    timestamp=datetime(2026, 3, 12, 9, index, tzinfo=UTC),
                )
            )

        await store.upsert_session("processed-session")
        await store.mark_session_gc_processed("processed-session")
        for index in range(5):
            session_memory.append(
                Message(
                    text=f"已处理会话 {index}",
                    sender="user",
                    session_id="processed-session",
                    timestamp=datetime(2026, 3, 1, 10, index, tzinfo=UTC),
                )
            )

        semantic_memory = StubSemanticMemory()
        router = StubModelRouter(text="# 空")
        gc = MemoryGC(
            session_memory=session_memory,
            structured_store=store,
            semantic_memory=semantic_memory,
            model_router=router,
            knowledge_dir=knowledge_dir,
            now_fn=lambda: datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        )

        result = await gc.run()

        assert result["processed_count"] == 0
        assert router.calls == []
        assert list((knowledge_dir / "gc_summaries").glob("*.md")) == []

    asyncio.run(_run())
