from __future__ import annotations

import asyncio
from pathlib import Path

import hypo_agent.memory.semantic_memory as semantic_memory_module
from hypo_agent.core.persona import PersonaManager
from hypo_agent.memory.semantic_memory import ChunkResult, SemanticMemory
from hypo_agent.memory.structured_store import StructuredStore


class StubEmbeddingRouter:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    float(text.count("简洁")),
                    float(text.count("咖啡")),
                    float(lowered.count("python")),
                    float(len(text.split())),
                ]
            )
        return vectors


def test_semantic_memory_splits_markdown_headings_and_long_windows(tmp_path: Path) -> None:
    store = StructuredStore(db_path=tmp_path / "hypo.db")
    memory = SemanticMemory(structured_store=store, model_router=StubEmbeddingRouter())
    long_body = " ".join(f"token{i}" for i in range(700))
    markdown = f"# 用户偏好\n\n## 回复风格\n\n用户喜欢简洁回复。\n\n### 长段落\n\n{long_body}\n"

    chunks = memory._chunk_markdown(markdown)

    assert len(chunks) >= 3
    assert chunks[0].heading_path == ["用户偏好", "回复风格"]
    assert "用户偏好 > 回复风格" in chunks[0].chunk_text
    assert any("长段落" in item.chunk_text for item in chunks[1:])


def test_semantic_memory_build_index_and_search_returns_relevant_results(
    tmp_path: Path,
) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "persona").mkdir()
    (knowledge_dir / "persona" / "user_preferences.md").write_text(
        """
# 用户偏好

## 回复风格

用户喜欢简洁回复，不要废话。
""".strip(),
        encoding="utf-8",
    )
    (knowledge_dir / "notes.md").write_text(
        """
# 杂项

## 饮品

用户最近在研究手冲咖啡。
""".strip(),
        encoding="utf-8",
    )

    async def _run() -> list[ChunkResult]:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        memory = SemanticMemory(structured_store=store, model_router=StubEmbeddingRouter())
        await memory.build_index(knowledge_dir)
        return await memory.search("请记住我喜欢简洁回复", top_k=3)

    results = asyncio.run(_run())

    assert results
    assert "简洁回复" in results[0].chunk_text
    assert results[0].file_path.endswith("user_preferences.md")


def test_semantic_memory_update_index_skips_unchanged_file(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    file_path = knowledge_dir / "profile.md"
    file_path.write_text("# 用户\n\n喜欢简洁。", encoding="utf-8")
    router = StubEmbeddingRouter()

    async def _run() -> int:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        memory = SemanticMemory(structured_store=store, model_router=router)
        await memory.update_index(file_path)
        await memory.update_index(file_path)
        return len(router.calls)

    call_count = asyncio.run(_run())

    assert call_count == 1


def test_semantic_memory_rrf_merge_prefers_dual_signal_matches() -> None:
    merged = SemanticMemory._rrf_merge(
        vector_hits=[
            ChunkResult(file_path="a.md", chunk_text="alpha", score=0.9, chunk_index=0),
            ChunkResult(file_path="b.md", chunk_text="beta", score=0.6, chunk_index=1),
        ],
        keyword_hits=[
            ChunkResult(file_path="b.md", chunk_text="beta", score=0.7, chunk_index=1),
            ChunkResult(file_path="c.md", chunk_text="gamma", score=0.5, chunk_index=2),
        ],
        top_k=3,
    )

    assert [item.file_path for item in merged] == ["b.md", "a.md", "c.md"]


def test_persona_manager_update_writes_l3_and_becomes_searchable(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    persona_path = config_dir / "persona.yaml"
    persona_path.write_text(
        """
name: Hypo
aliases: [assistant]
personality: [pragmatic]
speaking_style:
  tone: direct
""".strip(),
        encoding="utf-8",
    )
    knowledge_dir = tmp_path / "knowledge"
    router = StubEmbeddingRouter()

    async def _run() -> tuple[str, list[ChunkResult]]:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        semantic_memory = SemanticMemory(structured_store=store, model_router=router)
        manager = PersonaManager(
            persona_path=persona_path,
            semantic_memory=semantic_memory,
            knowledge_dir=knowledge_dir,
        )
        manager.load()
        await manager.update_persona_memory("response_style", "用户喜欢简洁回复")
        prompt = await manager.get_system_prompt_section(query="请用简洁方式回答")
        results = await semantic_memory.search("简洁 回复", top_k=3)
        return prompt, results

    prompt, results = asyncio.run(_run())

    assert "你是 Hypo" in prompt
    assert "用户喜欢简洁回复" in prompt
    assert any("简洁回复" in item.chunk_text for item in results)


def test_semantic_memory_search_logs_summary(tmp_path: Path) -> None:
    class RecordingLogger:
        def __init__(self) -> None:
            self.info_calls: list[tuple[str, dict]] = []
            self.warning_calls: list[tuple[str, dict]] = []

        def info(self, event: str, **kwargs) -> None:
            self.info_calls.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            self.warning_calls.append((event, kwargs))

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    file_path = knowledge_dir / "user_preferences.md"
    file_path.write_text(
        "# 用户偏好\n\n## 回复风格\n\n用户喜欢简洁回复。\n",
        encoding="utf-8",
    )
    router = StubEmbeddingRouter()
    logger = RecordingLogger()

    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        memory = SemanticMemory(structured_store=store, model_router=router)
        await memory.build_index(knowledge_dir)
        original_logger = semantic_memory_module.logger
        semantic_memory_module.logger = logger
        try:
            results = await memory.search("简洁 回复", top_k=3)
        finally:
            semantic_memory_module.logger = original_logger
        assert results

    asyncio.run(_run())

    assert logger.warning_calls == []
    assert logger.info_calls
    event, payload = logger.info_calls[-1]
    assert event == "semantic_memory.search"
    assert payload["vector_hits"] >= 1
    assert payload["keyword_hits"] >= 0
    assert payload["final_results"] >= 1
    assert payload["top_score"] > 0.0


def test_semantic_memory_update_index_logs_changed_and_skipped_file(tmp_path: Path) -> None:
    class RecordingLogger:
        def __init__(self) -> None:
            self.info_calls: list[tuple[str, dict]] = []
            self.debug_calls: list[tuple[str, dict]] = []
            self.warning_calls: list[tuple[str, dict]] = []

        def info(self, event: str, **kwargs) -> None:
            self.info_calls.append((event, kwargs))

        def debug(self, event: str, **kwargs) -> None:
            self.debug_calls.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            self.warning_calls.append((event, kwargs))

        def exception(self, event: str, **kwargs) -> None:
            self.warning_calls.append((event, kwargs))

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    file_path = knowledge_dir / "profile.md"
    file_path.write_text("# 用户\n\n喜欢简洁。", encoding="utf-8")
    router = StubEmbeddingRouter()
    logger = RecordingLogger()

    async def _run() -> None:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        memory = SemanticMemory(structured_store=store, model_router=router)
        original_logger = semantic_memory_module.logger
        semantic_memory_module.logger = logger
        try:
            await memory.update_index(file_path)
            await memory.update_index(file_path)
        finally:
            semantic_memory_module.logger = original_logger

    asyncio.run(_run())

    assert logger.warning_calls == []
    assert logger.info_calls
    assert logger.info_calls[0][0] == "semantic_memory.index_update"
    assert logger.info_calls[0][1]["file_path"] == str(file_path.resolve(strict=False))
    assert logger.info_calls[0][1]["chunks"] >= 1
    assert logger.debug_calls == [
        (
            "semantic_memory.index_skip",
            {"file_path": str(file_path.resolve(strict=False)), "reason": "hash_unchanged"},
        )
    ]
