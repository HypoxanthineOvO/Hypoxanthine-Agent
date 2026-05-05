from __future__ import annotations

import asyncio
from datetime import datetime


def _rt(text: str) -> dict:
    return {"type": "text", "plain_text": text, "annotations": {}}


class PlanSkillClient:
    def __init__(self) -> None:
        self.children = {
            "plan": [
                {"id": "month", "type": "child_page", "child_page": {"title": "2026年5月"}},
            ],
            "month": [
                {"id": "h-0508", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月8日")]}},
            ],
        }
        self.append_calls: list[dict] = []

    async def get_page_content(self, page_id: str) -> list[dict]:
        return list(self.children[page_id])

    async def append_blocks(self, page_id: str, blocks: list[dict], *, after: str | None = None) -> None:
        self.append_calls.append({"page_id": page_id, "blocks": blocks, "after": after})


def test_notion_plan_skill_exposes_dedicated_tools(tmp_path) -> None:
    from hypo_agent.skills.notion_plan_skill import NotionPlanSkill

    skill = NotionPlanSkill(
        notion_client=PlanSkillClient(),
        plan_page_id="plan",
        knowledge_dir=tmp_path,
        now_fn=lambda: datetime(2026, 5, 5, 12, 0),
    )

    names = {tool["function"]["name"] for tool in skill.tools}

    assert names == {
        "notion_plan_get_today",
        "notion_plan_get_structure",
        "notion_plan_add_items",
    }


def test_notion_plan_add_items_returns_replay_summary(tmp_path) -> None:
    from hypo_agent.skills.notion_plan_skill import NotionPlanSkill

    skill = NotionPlanSkill(
        notion_client=PlanSkillClient(),
        plan_page_id="plan",
        knowledge_dir=tmp_path,
        now_fn=lambda: datetime(2026, 5, 5, 12, 0),
    )

    async def _run():
        await skill.execute("notion_plan_get_structure", {})
        return await skill.execute(
            "notion_plan_add_items",
            {"text": "5/8 10:30-11:30 普拉提训练", "dry_run": False},
        )

    result = asyncio.run(_run())

    assert result.status == "success"
    assert "已加入计划通：5/8 10:30-11:30 普拉提训练" in str(result.result)
    assert "插入位置：\n2026年5月 / 5月8日" in str(result.result)
