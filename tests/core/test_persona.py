from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.persona import PersonaManager
from hypo_agent.memory.semantic_memory import ChunkResult


class StubSemanticMemory:
    def __init__(self, results: list[ChunkResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[str] = []
        self.updated_paths: list[Path] = []

    async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
        del top_k
        self.calls.append(query)
        return list(self.results)

    async def update_index(self, file_path: Path | str) -> None:
        self.updated_paths.append(Path(file_path))


def test_persona_manager_loads_config_and_builds_prompt(tmp_path: Path) -> None:
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text(
        """
name: Hypo
aliases: [assistant, hypo]
personality: [pragmatic, concise]
speaking_style:
  tone: direct
  habits:
    - 回答完直接结束
    - 不主动给下一步建议
""".strip(),
        encoding="utf-8",
    )
    semantic_memory = StubSemanticMemory(
        [
            ChunkResult(
                file_path="memory/knowledge/persona/user_preferences.md",
                chunk_text="用户喜欢简洁回复。",
                score=0.8,
                chunk_index=0,
            )
        ]
    )
    manager = PersonaManager(
        persona_path=persona_path,
        semantic_memory=semantic_memory,
        knowledge_dir=tmp_path / "knowledge",
    )
    manager.load()

    prompt = asyncio.run(manager.get_system_prompt_section(query="请直接回答"))

    assert "你是 Hypo（assistant, hypo）" in prompt
    assert "pragmatic；concise" in prompt
    assert "说话风格：direct" in prompt
    assert "行为边界：" in prompt
    assert "- 回答完直接结束" in prompt
    assert "用户喜欢简洁回复" in prompt
    assert semantic_memory.calls == ["请直接回答"]
