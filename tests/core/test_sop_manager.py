from __future__ import annotations

import asyncio
from pathlib import Path

import hypo_agent.core.sop_manager as sop_manager_module
from hypo_agent.core.sop_manager import SopManager


class StubSemanticMemory:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.update_calls: list[str] = []

    async def update_index(self, file_path) -> None:
        self.update_calls.append(str(file_path))

    async def search(self, query: str, top_k: int = 5, **kwargs):
        del query, top_k, kwargs
        return list(self.results)


class StubChunkResult:
    def __init__(self, file_path: str, chunk_text: str, score: float = 0.9, chunk_index: int = 0) -> None:
        self.file_path = file_path
        self.chunk_text = chunk_text
        self.score = score
        self.chunk_index = chunk_index


def test_sop_manager_requires_confirmation_before_save(tmp_path: Path) -> None:
    async def _run() -> None:
        manager = SopManager(
            knowledge_dir=tmp_path / "knowledge",
            semantic_memory=StubSemanticMemory(),
        )

        result = await manager.save_sop(
            title="部署流程",
            content="1. 拉取最新代码\n2. 重启服务",
            confirm=False,
        )

        assert result.status == "success"
        assert result.result["requires_confirmation"] is True
        assert (tmp_path / "knowledge" / "sop" / "部署流程.md").exists() is False

    asyncio.run(_run())


def test_sop_manager_saves_markdown_and_updates_semantic_index(tmp_path: Path) -> None:
    async def _run() -> None:
        semantic_memory = StubSemanticMemory()
        manager = SopManager(
            knowledge_dir=tmp_path / "knowledge",
            semantic_memory=semantic_memory,
        )

        result = await manager.save_sop(
            title="部署流程",
            content="1. 拉取最新代码\n2. 重启服务",
            confirm=True,
        )

        assert result.status == "success"
        file_path = tmp_path / "knowledge" / "sop" / "部署流程.md"
        assert file_path.exists() is True
        content = file_path.read_text(encoding="utf-8")
        assert "# SOP: 部署流程" in content
        assert "## 元信息" in content
        assert "- 使用次数: 0" in content
        assert semantic_memory.update_calls == [str(file_path)]

    asyncio.run(_run())


def test_sop_manager_search_returns_summaries_for_matching_sops(tmp_path: Path) -> None:
    async def _run() -> None:
        file_path = tmp_path / "knowledge" / "sop" / "部署流程.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            """
# SOP: 部署流程

## 适用场景

发布 FastAPI 服务。

## 前置条件

已拿到服务器权限。

## 步骤

1. 拉取最新代码
2. 执行迁移
3. 重启服务

## 注意事项

确认端口未被占用。

## 元信息

- 创建时间: 2026-03-15T00:00:00+00:00
- 最后使用: 2026-03-15T00:00:00+00:00
- 使用次数: 0
""".strip(),
            encoding="utf-8",
        )
        semantic_memory = StubSemanticMemory(
            [
                StubChunkResult(
                    file_path=str(file_path),
                    chunk_text="标题上下文：SOP: 部署流程 > 步骤\n\n1. 拉取最新代码\n2. 执行迁移",
                )
            ]
        )
        manager = SopManager(
            knowledge_dir=tmp_path / "knowledge",
            semantic_memory=semantic_memory,
        )

        result = await manager.search_sop(query="怎么部署服务", top_k=3)

        assert result.status == "success"
        assert result.result["items"][0]["title"] == "部署流程"
        assert "发布 FastAPI 服务" in result.result["items"][0]["applicable_scenario"]
        assert "拉取最新代码" in result.result["items"][0]["steps_summary"]

    asyncio.run(_run())


def test_sop_manager_updates_usage_metadata(tmp_path: Path) -> None:
    class RecordingLogger:
        def __init__(self) -> None:
            self.info_calls: list[tuple[str, dict]] = []

        def info(self, event: str, **kwargs) -> None:
            self.info_calls.append((event, kwargs))

    async def _run() -> None:
        file_path = tmp_path / "knowledge" / "sop" / "部署流程.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            """
# SOP: 部署流程

## 适用场景

发布 FastAPI 服务。

## 前置条件

已拿到服务器权限。

## 步骤

1. 拉取最新代码

## 注意事项

确认端口未被占用。

## 元信息

- 创建时间: 2026-03-14T00:00:00+00:00
- 最后使用: 2026-03-14T00:00:00+00:00
- 使用次数: 0
""".strip(),
            encoding="utf-8",
        )
        semantic_memory = StubSemanticMemory()
        manager = SopManager(
            knowledge_dir=tmp_path / "knowledge",
            semantic_memory=semantic_memory,
        )
        logger = RecordingLogger()
        original_logger = sop_manager_module.logger
        sop_manager_module.logger = logger
        try:
            updated = await manager.update_sop_metadata("部署流程")
        finally:
            sop_manager_module.logger = original_logger

        assert updated is True
        content = file_path.read_text(encoding="utf-8")
        assert "- 使用次数: 1" in content
        assert "- 最后使用: 2026-03-14T00:00:00+00:00" not in content
        assert semantic_memory.update_calls == [str(file_path)]
        assert logger.info_calls == [
            (
                "sop.metadata_updated",
                {"title": "部署流程", "usage_count": 1},
            )
        ]

    asyncio.run(_run())
