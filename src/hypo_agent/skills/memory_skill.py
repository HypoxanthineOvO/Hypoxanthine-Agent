from __future__ import annotations

import inspect
from typing import Any

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class MemorySkill(BaseSkill):
    name = "memory"
    description = "Persist and retrieve user preferences in L2 memory."
    required_permissions: list[str] = []

    def __init__(self, *, structured_store: Any) -> None:
        self.structured_store = structured_store

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "save_preference",
                    "description": (
                        "Save a stable user preference/habit/personal detail to persistent memory. "
                        "Use this when the user expresses something like preferred language, tone, "
                        "timezone, likes/dislikes, or recurring habits."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "Preference key, e.g. 'language' or 'timezone'.",
                            },
                            "value": {
                                "type": "string",
                                "description": "Preference value to store.",
                            },
                        },
                        "required": ["key", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_preference",
                    "description": "Read a preference from persistent memory by key.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                        },
                        "required": ["key"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "save_preference":
            return await self._save_preference(params)
        if tool_name == "get_preference":
            return await self._get_preference(params)
        return SkillOutput(
            status="error",
            error_info=f"Unsupported tool '{tool_name}' for memory skill",
        )

    async def _save_preference(self, params: dict[str, Any]) -> SkillOutput:
        key = str(params.get("key") or "").strip()
        value = str(params.get("value") or "").strip()
        if not key:
            return SkillOutput(status="error", error_info="key is required")
        if not value:
            return SkillOutput(status="error", error_info="value is required")

        saver = getattr(self.structured_store, "save_preference", None)
        if callable(saver):
            result = saver(key, value)
            if inspect.isawaitable(result):
                await result
        else:
            setter = getattr(self.structured_store, "set_preference", None)
            if not callable(setter):
                return SkillOutput(status="error", error_info="structured_store does not support preferences")
            result = setter(key, value)
            if inspect.isawaitable(result):
                await result

        return SkillOutput(status="success", result={"key": key, "value": value})

    async def _get_preference(self, params: dict[str, Any]) -> SkillOutput:
        key = str(params.get("key") or "").strip()
        if not key:
            return SkillOutput(status="error", error_info="key is required")

        getter = getattr(self.structured_store, "get_preference", None)
        if not callable(getter):
            return SkillOutput(status="error", error_info="structured_store does not support preferences")
        value = getter(key)
        if inspect.isawaitable(value):
            value = await value

        return SkillOutput(status="success", result={"key": key, "value": value})
