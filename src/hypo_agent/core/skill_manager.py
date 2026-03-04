from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class SkillManager:
    def __init__(
        self,
        skills: list[BaseSkill] | None = None,
        *,
        circuit_breaker: Any | None = None,
    ) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self._tool_to_skill: dict[str, BaseSkill] = {}
        self._circuit_breaker = circuit_breaker
        if skills:
            self.register_many(skills)

    def register(self, skill: BaseSkill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' already registered")

        self._skills[skill.name] = skill
        for tool in skill.tools:
            tool_name = self._read_tool_name(tool)
            if not tool_name:
                raise ValueError(f"Skill '{skill.name}' has tool without function.name")
            if tool_name in self._tool_to_skill:
                raise ValueError(f"Tool '{tool_name}' already registered")
            self._tool_to_skill[tool_name] = skill

    def register_many(self, skills: list[BaseSkill]) -> None:
        for skill in skills:
            self.register(skill)

    def get_tools_schema(self) -> list[dict[str, Any]]:
        all_tools: list[dict[str, Any]] = []
        for skill in self._skills.values():
            all_tools.extend(skill.tools)
        return all_tools

    @staticmethod
    def find_enabled_skills(path: Path | str = "config/skills.yaml") -> set[str]:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            return set()

        configured_skills = payload.get("skills", {})
        if not isinstance(configured_skills, dict):
            return set()

        enabled: set[str] = set()
        for name, cfg in configured_skills.items():
            if isinstance(cfg, dict) and bool(cfg.get("enabled", False)):
                enabled.add(str(name))
        return enabled

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> SkillOutput:
        if self._circuit_breaker is not None:
            allowed, reason = self._circuit_breaker.can_execute(tool_name, session_id)
            if not allowed:
                return SkillOutput(status="error", error_info=reason)

        skill = self._tool_to_skill.get(tool_name)
        if skill is None:
            return SkillOutput(
                status="error",
                error_info=f"Unknown tool '{tool_name}'",
            )

        try:
            result = await skill.execute(tool_name, params)
        except Exception as exc:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(tool_name, session_id)
            return SkillOutput(status="error", error_info=str(exc))

        if not isinstance(result, SkillOutput):
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(tool_name, session_id)
            return SkillOutput(
                status="error",
                error_info=f"Skill '{skill.name}' returned invalid output",
            )

        if self._circuit_breaker is not None:
            if result.status == "success":
                self._circuit_breaker.record_success(tool_name, session_id)
            else:
                self._circuit_breaker.record_failure(tool_name, session_id)

        return result

    def _read_tool_name(self, tool: dict[str, Any]) -> str:
        function_payload = tool.get("function")
        if not isinstance(function_payload, dict):
            return ""
        name = function_payload.get("name")
        return str(name) if isinstance(name, str) else ""
