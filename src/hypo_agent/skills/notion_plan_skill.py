from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from hypo_agent.core.notion_plan import NotionPlanReader
from hypo_agent.core.notion_plan_editor import NotionPlanEditor, parse_plan_items
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class NotionPlanSkill(BaseSkill):
    name = "notion-plan"
    description = "读取和编辑 Notion Plan 计划通，支持按日期定位、插入日程和结构预览。"
    keyword_hints = ["计划通", "notion plan", "加到计划通", "插入计划通", "今日计划"]
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        notion_client: Any,
        plan_page_id: str = "",
        plan_title: str = "HYX的计划通",
        root_title: str = "",
        semester_title: str = "",
        knowledge_dir: Path | str = "memory/knowledge/notion-plan",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = notion_client
        self.plan_page_id = str(plan_page_id or "").strip()
        self.plan_title = plan_title
        self.root_title = root_title
        self.semester_title = semester_title
        self.knowledge_dir = Path(knowledge_dir)
        self.now_fn = now_fn or datetime.now

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "notion_plan_get_today",
                    "description": "Read today's HYX Notion Plan summary.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_plan_get_structure",
                    "description": "Read and persist the Notion Plan page/month/date structure knowledge.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_plan_add_items",
                    "description": "Add one or more dated schedule items into HYX Notion Plan.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "dry_run": {"type": "boolean", "default": False},
                        },
                        "required": ["text"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "notion_plan_get_today":
                return SkillOutput(status="success", result=await self.get_today())
            if tool_name == "notion_plan_get_structure":
                return SkillOutput(status="success", result=await self.get_structure())
            if tool_name == "notion_plan_add_items":
                text = str(params.get("text") or "").strip()
                if not text:
                    return SkillOutput(status="error", error_info="text is required")
                return SkillOutput(status="success", result=await self.add_items(text, dry_run=bool(params.get("dry_run", False))))
        except Exception as exc:  # noqa: BLE001 - skill boundary
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def get_today(self) -> dict[str, Any]:
        reader = NotionPlanReader(
            notion_client=self._client,
            plan_page_id=self.plan_page_id,
            root_title=self.root_title,
            plan_title=self.plan_title,
            semester_title=self.semester_title,
        )
        return (await reader.read_today(today=self.now_fn().date())).to_payload()

    async def get_structure(self) -> dict[str, Any]:
        await self._ensure_plan_page_id()
        editor = self._editor()
        structure = await editor.discover_structure()
        editor.write_knowledge(self.knowledge_dir)
        return structure

    async def add_items(self, text: str, *, dry_run: bool = False) -> str:
        await self._ensure_plan_page_id()
        editor = self._editor()
        if not editor.structure.get("month_pages"):
            await editor.discover_structure()
            editor.write_knowledge(self.knowledge_dir)
        parsed = parse_plan_items(text, default_year=self.now_fn().year)
        if dry_run:
            preview = await editor.preview_add_items(parsed.items)
            preview.failed_items.extend(parsed.failed_items)
            return preview.human_summary
        return (await editor.add_items(parsed)).human_summary

    async def _ensure_plan_page_id(self) -> str:
        if self.plan_page_id:
            return self.plan_page_id
        reader = NotionPlanReader(
            notion_client=self._client,
            plan_page_id="",
            root_title=self.root_title,
            plan_title=self.plan_title,
            semester_title=self.semester_title,
        )
        resolver = getattr(reader, "_resolve_plan_page_id")
        self.plan_page_id = str(await resolver()).strip()
        return self.plan_page_id

    def _editor(self) -> NotionPlanEditor:
        return NotionPlanEditor(
            notion_client=self._client,
            plan_page_id=self.plan_page_id,
            default_year=self.now_fn().year,
            semester_title=self.semester_title,
            structure_path=self.knowledge_dir / "structure.json",
        )
