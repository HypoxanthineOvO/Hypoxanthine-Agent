from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class MemorySkill(BaseSkill):
    name = "memory"
    description = "Persist and retrieve structured user memory in L2."
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
                        "Save a stable structured memory item to L2 persistent memory. "
                        "Use this for durable preferences, habits, profile details, or other "
                        "reusable key-value memory. After calling it, explicitly tell the user "
                        "which database file and folder were updated."
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
                    "description": "Read a structured memory item from persistent L2 memory by key.",
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

        storage_path = Path(getattr(self.structured_store, "db_path"))
        storage_folder = storage_path.parent
        return SkillOutput(
            status="success",
            result={
                "key": key,
                "value": value,
                "storage_path": str(storage_path),
                "storage_folder": str(storage_folder),
                "human_summary": (
                    f"已写入结构化记忆：{key}={value}。"
                    f"数据库文件在 {storage_path}，所在文件夹是 {storage_folder}。"
                ),
            },
        )

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
