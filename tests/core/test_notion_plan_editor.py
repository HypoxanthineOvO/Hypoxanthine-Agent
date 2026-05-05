from __future__ import annotations

import asyncio
from datetime import date
import json


def _rt(text: str) -> dict:
    return {"type": "text", "plain_text": text, "annotations": {}}


class RecordingPlanClient:
    def __init__(self) -> None:
        self.children: dict[str, list[dict]] = {
            "plan": [
                {"id": "term-heading", "type": "heading_1", "heading_1": {"rich_text": [_rt("研一下")]}},
                {"id": "month-may", "type": "child_page", "child_page": {"title": "五月"}},
            ],
            "month-may": [
                {"id": "h-0508", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月8日")]}},
                {"id": "todo-0900", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("组会（09:00-10:00）")]}},
                {"id": "todo-1400", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("阅读（14:00-15:00）")]}},
                {"id": "todo-note", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("整理材料")]}},
                {"id": "h-0509", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月9日")]}},
            ],
        }
        self.append_calls: list[dict] = []
        self.create_child_page_calls: list[dict] = []

    async def get_page_content(self, page_id: str) -> list[dict]:
        return list(self.children[page_id])

    async def append_blocks(self, page_id: str, blocks: list[dict], *, after: str | None = None) -> None:
        self.append_calls.append({"page_id": page_id, "blocks": blocks, "after": after})

    async def create_child_page(self, parent_page_id: str, title: str) -> dict:
        page_id = f"created-{title}"
        self.create_child_page_calls.append({"parent_page_id": parent_page_id, "title": title})
        self.children[page_id] = []
        return {"id": page_id, "title": title}


class AcademicPlanClient:
    def __init__(self) -> None:
        self.children: dict[str, list[dict]] = {
            "plan": [
                {"id": "h-d2-down", "type": "heading_1", "heading_1": {"rich_text": [_rt("大二下")]}},
                {"id": "may-d2", "type": "child_page", "child_page": {"title": "五月"}},
                {"id": "h-grad1-down", "type": "heading_1", "heading_1": {"rich_text": [_rt("研一下")]}},
                {"id": "jan-grad1", "type": "child_page", "child_page": {"title": "一月"}},
                {"id": "may-grad1", "type": "child_page", "child_page": {"title": "五月"}},
            ],
            "may-d2": [
                {"id": "h-old-0508", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月8日")]}},
                {"id": "old-item", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("旧五月事项")]}},
            ],
            "may-grad1": [
                {"id": "h-current-0508", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月8日")]}},
                {"id": "current-item", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("CS110 Lab 11")]}},
            ],
        }

    async def get_page_content(self, page_id: str) -> list[dict]:
        return list(self.children[page_id])


def test_parse_plan_items_supports_multi_item_no_time_and_cross_day() -> None:
    from hypo_agent.core.notion_plan_editor import parse_plan_items

    result = parse_plan_items(
        "5/8 10:30-11:30 普拉提训练\n"
        "5/8 11:00 抗焦虑药\n"
        "5月9日上午9:30–10:15，信院国际评估Student Poster讲解，重要！！\n"
        "5/8 整理材料\n"
        "5/8 22:00-5/9 01:00 写作",
        default_year=2026,
    )

    assert result.failed_items == []
    assert [(item.title, item.target_date.isoformat(), item.display_time_range, item.display_text) for item in result.items] == [
        ("普拉提训练", "2026-05-08", "10:30-11:30", "普拉提训练（10:30-11:30）"),
        ("抗焦虑药", "2026-05-08", "11:00", "抗焦虑药（11:00）"),
        ("信院国际评估 Student Poster 讲解", "2026-05-09", "9:30–10:15", "信院国际评估 Student Poster 讲解（9:30–10:15）"),
        ("整理材料", "2026-05-08", "", "整理材料"),
        ("写作", "2026-05-08", "22:00-5/9 1:00", "写作（22:00-5/9 1:00）"),
    ]
    assert result.items[2].important is True
    assert result.items[2].user_text == "5/9 **信院国际评估 Student Poster 讲解（9:30–10:15）**"
    assert result.items[3].sort_key > result.items[0].sort_key


def test_plan_editor_preview_locates_insert_position_and_missing_date() -> None:
    from hypo_agent.core.notion_plan_editor import NotionPlanEditor, parse_plan_items

    client = RecordingPlanClient()
    editor = NotionPlanEditor(
        notion_client=client,
        plan_page_id="plan",
        default_year=2026,
        structure={
            "month_pages": [{"year": 2026, "month": 5, "page_id": "month-may", "title": "2026年5月"}],
            "date_heading_format": "{month}月{day}日",
            "academic_anchors": {"大一上": "2021-09", "研一上": "2025-09"},
        },
    )

    result = asyncio.run(editor.preview_add_items(parse_plan_items("5/8 10:30-11:30 普拉提训练", default_year=2026).items))

    assert result.planned[0].target_month_page_id == "month-may"
    assert result.planned[0].date_block_id == "h-0508"
    assert result.planned[0].insert_after_block_id == "todo-0900"
    assert result.planned[0].insert_before_title == "阅读（14:00-15:00）"
    assert "5/8 普拉提训练（10:30-11:30） -> 2026年5月/5月8日 阅读（14:00-15:00）之前" in result.human_summary

    first = asyncio.run(editor.preview_add_items(parse_plan_items("5/8 08:00-09:00 跑步", default_year=2026).items))
    assert first.planned[0].insert_after_block_id == "h-0508"
    assert first.planned[0].insert_before_title == "组会（09:00-10:00）"

    missing_date = asyncio.run(editor.preview_add_items(parse_plan_items("5/10 08:00-09:00 跑步", default_year=2026).items))
    assert missing_date.planned[0].date_needs_create is True
    assert missing_date.planned[0].target_date_heading == "5月10日"


def test_plan_editor_writes_successful_items_and_skips_duplicates(tmp_path) -> None:
    from hypo_agent.core.notion_plan_editor import NotionPlanEditor, parse_plan_items

    client = RecordingPlanClient()
    structure_path = tmp_path / "structure.json"
    structure_path.write_text(
        json.dumps(
            {
                "structure_version": 2,
                "month_pages": [{"year": 2026, "month": 5, "page_id": "month-may", "title": "五月", "semester": "研一下"}],
                "date_heading_format": "{month}月{day}日",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    editor = NotionPlanEditor(
        notion_client=client,
        plan_page_id="plan",
        structure_path=structure_path,
        default_year=2026,
    )

    result = asyncio.run(editor.add_items(parse_plan_items("5/8 10:30-11:30 普拉提训练\n5/8 11:00 重要 抗焦虑药\nbad line", default_year=2026)))

    assert result.success_count == 2
    assert result.failure_count == 1
    assert client.append_calls[0]["page_id"] == "month-may"
    assert client.append_calls[0]["after"] == "todo-0900"
    inserted = client.append_calls[0]["blocks"][0]
    assert inserted["type"] == "to_do"
    assert inserted["to_do"]["rich_text"][0]["text"]["content"] == "普拉提训练（10:30-11:30）"
    important = client.append_calls[1]["blocks"][0]
    assert important["to_do"]["rich_text"][0]["text"]["content"] == "抗焦虑药（11:00）"
    assert important["to_do"]["rich_text"][0]["annotations"]["bold"] is True
    assert "已加入计划通：5/8 普拉提训练（10:30-11:30）" in result.human_summary
    assert "已加入计划通：5/8 **抗焦虑药（11:00）**" in result.human_summary
    assert "插入位置：研一下/五月/5月8日 阅读（14:00-15:00）之前" in result.human_summary

    duplicate = asyncio.run(editor.add_items(parse_plan_items("5/8 10:30-11:30 普拉提训练", default_year=2026)))
    assert duplicate.skipped_count == 1
    assert len(client.append_calls) == 2
    assert duplicate.human_summary == "\n\n".join(
        [
            "计划通已存在，跳过重复：5/8 普拉提训练（10:30-11:30）",
            "插入位置：研一下/五月/5月8日 已存在，跳过重复写入",
            "当天日程：\n- 组会（09:00-10:00）\n- 阅读（14:00-15:00）\n- 整理材料",
        ]
    )


def test_plan_editor_discovers_academic_semester_years_and_targets_current_may() -> None:
    from hypo_agent.core.notion_plan_editor import NotionPlanEditor, parse_plan_items

    client = AcademicPlanClient()
    editor = NotionPlanEditor(notion_client=client, plan_page_id="plan", default_year=2026)

    structure = asyncio.run(editor.discover_structure())

    month_pages = {
        (item["semester"], item["year"], item["month"]): item["page_id"]
        for item in structure["month_pages"]
    }
    assert month_pages[("大二下", 2023, 5)] == "may-d2"
    assert month_pages[("研一下", 2026, 1)] == "jan-grad1"
    assert month_pages[("研一下", 2026, 5)] == "may-grad1"

    preview = asyncio.run(
        editor.preview_add_items(parse_plan_items("5/8 10:30-11:30 普拉提训练", default_year=2026).items)
    )

    assert preview.planned[0].target_month_page_id == "may-grad1"
    assert preview.planned[0].date_block_id == "h-current-0508"
    assert preview.planned[0].existing_titles == ["CS110 Lab 11"]
