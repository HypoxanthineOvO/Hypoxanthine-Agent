from __future__ import annotations

from pathlib import Path
from typing import Any

from hypo_agent.core.config_loader import (
    get_memory_dir,
    load_persona_config,
    normalize_speaking_style_habits,
    render_persona_system_prompt,
)
from hypo_agent.memory.semantic_memory import SemanticMemory
from hypo_agent.models import PersonaConfig


class PersonaManager:
    def __init__(
        self,
        *,
        persona_path: Path | str = "config/persona.yaml",
        semantic_memory: SemanticMemory | Any | None = None,
        knowledge_dir: Path | str | None = None,
    ) -> None:
        self.persona_path = Path(persona_path)
        self.semantic_memory = semantic_memory
        self.knowledge_dir = (
            Path(knowledge_dir)
            if knowledge_dir is not None
            else get_memory_dir() / "knowledge"
        )
        self._config: PersonaConfig | None = None

    def load(self) -> PersonaConfig:
        self._config = load_persona_config(self.persona_path)
        return self._config

    async def get_system_prompt_section(self, query: str | None = None) -> str:
        config = self._config or self.load()

        static_section = self._build_static_section(config)
        dynamic_chunks: list[str] = []
        if self.semantic_memory is not None:
            search_query = str(query or "").strip() or SemanticMemory.default_user_query()
            try:
                results = await self.semantic_memory.search(search_query, top_k=5)
            except (OSError, RuntimeError, TypeError, ValueError):
                results = []
            dynamic_chunks = [item.chunk_text for item in results if item.chunk_text.strip()]

        known_info = "\n\n---\n\n".join(dynamic_chunks).strip() or "暂无。"
        return (
            f"{static_section}\n\n---\n\n"
            f"关于用户的已知信息：\n\n{known_info}"
        ).strip()

    async def update_persona_memory(self, key: str, value: str) -> dict[str, str]:
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if not normalized_key:
            raise ValueError("key is required")
        if not normalized_value:
            raise ValueError("value is required")

        file_path = self.knowledge_dir / "persona" / "user_preferences.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        existing = file_path.read_text(encoding="utf-8") if file_path.exists() else "# 用户偏好\n"
        entry = f"\n\n## {normalized_key}\n\n{normalized_value}\n"
        if not existing.endswith("\n"):
            existing += "\n"
        file_path.write_text(existing + entry, encoding="utf-8")

        if self.semantic_memory is not None:
            await self.semantic_memory.update_index(file_path)

        return {
            "file_path": str(file_path),
            "folder_path": str(file_path.parent),
            "key": normalized_key,
            "value": normalized_value,
            "human_summary": (
                f"已写入长期记忆：{normalized_key}={normalized_value}。"
                f"文件在 {file_path}，所在文件夹是 {file_path.parent}。"
            ),
        }

    def _build_static_section(self, config: PersonaConfig) -> str:
        template = str(config.system_prompt_template or "").strip()
        if template:
            return render_persona_system_prompt(config).strip()

        aliases = ", ".join(config.aliases)
        alias_part = f"（{aliases}）" if aliases else ""
        personality = "；".join(str(item).strip() for item in config.personality if str(item).strip())
        tone = str(config.speaking_style.get("tone") or "").strip()
        habits = normalize_speaking_style_habits(config.speaking_style)

        lines = [f"你是 {config.name}{alias_part}。"]
        if personality:
            lines.append(personality)
        if tone:
            lines.append(f"说话风格：{tone}")
        if habits:
            lines.append("行为边界：")
            lines.extend(f"- {item}" for item in habits)
        return "\n\n".join(lines).strip()
