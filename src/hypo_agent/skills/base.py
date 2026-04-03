from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from hypo_agent.models import SkillOutput

DEFAULT_MAX_OUTPUT_CHARS = 262144


class BaseSkill(ABC):
    name: str
    description: str
    required_permissions: list[str]

    @property
    @abstractmethod
    def tools(self) -> list[dict[str, Any]]:
        """Return tool schemas in OpenAI function-calling format."""

    @abstractmethod
    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        """Execute a tool call and return normalized skill output."""
