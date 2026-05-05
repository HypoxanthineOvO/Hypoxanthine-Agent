from __future__ import annotations

import asyncio
from datetime import date


def _rt(text: str, *, bold: bool = False, color: str = "default") -> dict:
    return {
        "type": "text",
        "plain_text": text,
        "annotations": {"bold": bold, "color": color},
    }


class FakePlanClient:
    def __init__(self) -> None:
        self.children = {
            "root": [
                {"id": "plan", "type": "child_page", "child_page": {"title": "HYX的计划通"}},
            ],
            "plan": [
                {"id": "semester", "type": "child_page", "child_page": {"title": "研一下"}},
            ],
            "semester": [
                {"id": "month", "type": "child_page", "child_page": {"title": "2026年5月"}},
            ],
            "month": [
                {"id": "h-today", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月5日")]}},
                {"id": "todo-1", "type": "to_do", "to_do": {"checked": True, "rich_text": [_rt("完成论文")] }},
                {"id": "todo-2", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("准备组会", bold=True)]}},
                {"id": "todo-3", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rt("红色提醒", color="red")]}},
                {"id": "h-other", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月6日")]}},
                {"id": "todo-4", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("明天")] }},
            ],
        }

    async def search(self, query: str, object_type: str | None = None, page_size: int = 10) -> list[dict]:
        del object_type, page_size
        if query == "Hypoxanthine's Home":
            return [{"id": "root", "object": "page", "properties": {"title": {"title": [_rt("Hypoxanthine's Home")]}}}]
        return []

    async def get_page_content(self, page_id: str) -> list[dict]:
        return list(self.children[page_id])


class FakePlanClientWithoutRoot(FakePlanClient):
    async def search(self, query: str, object_type: str | None = None, page_size: int = 10) -> list[dict]:
        del object_type, page_size
        if query == "Hypoxanthine's Home":
            return []
        if query == "HYX的计划通":
            return [{"id": "plan", "object": "page", "properties": {"title": {"title": [_rt("HYX 的计划通")]}}}]
        return []


class FakePlanClientWithSemesterHeading(FakePlanClient):
    def __init__(self) -> None:
        super().__init__()
        self.children["plan"] = [
            {"id": "h-semester", "type": "heading_1", "heading_1": {"rich_text": [_rt("研一下")]}},
            {"id": "month", "type": "child_page", "child_page": {"title": "五月"}},
            {"id": "h-next", "type": "heading_1", "heading_1": {"rich_text": [_rt("研二上")]}},
            {"id": "wrong-month", "type": "child_page", "child_page": {"title": "五月"}},
        ]


class FakePlanClientWithConfiguredPageId(FakePlanClient):
    def __init__(self) -> None:
        super().__init__()
        self.search_calls: list[str] = []
        self.children["configured-plan"] = [
            {"id": "month", "type": "child_page", "child_page": {"title": "五月"}},
        ]

    async def search(self, query: str, object_type: str | None = None, page_size: int = 10) -> list[dict]:
        del object_type, page_size
        self.search_calls.append(query)
        return []


class FakePlanClientWithDiscoverableMonth(FakePlanClient):
    def __init__(self) -> None:
        super().__init__()
        self.children = {
            "plan": [
                {"id": "archive", "type": "child_page", "child_page": {"title": "归档"}},
                {"id": "branch", "type": "child_page", "child_page": {"title": "当前计划"}},
            ],
            "archive": [
                {"id": "old-month", "type": "child_page", "child_page": {"title": "五月"}},
            ],
            "old-month": [
                {"id": "old-heading", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月4日")]}},
            ],
            "branch": [
                {"id": "real-month", "type": "child_page", "child_page": {"title": "May"}},
            ],
            "real-month": [
                {"id": "h-today", "type": "heading_1", "heading_1": {"rich_text": [_rt("2026-05-05")]}},
                {"id": "todo", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("无硬编码发现")]}},
            ],
        }

    async def search(self, query: str, object_type: str | None = None, page_size: int = 10) -> list[dict]:
        del object_type, page_size
        if query == "HYX的计划通":
            return [{"id": "plan", "object": "page", "properties": {"title": {"title": [_rt("HYX的计划通")]}}}]
        return []


class FakePlanClientWithRepeatedMonthPages(FakePlanClient):
    def __init__(self) -> None:
        super().__init__()
        self.children = {
            "plan": [
                {"id": "old-may", "type": "child_page", "child_page": {"title": "五月"}},
                {"id": "june", "type": "child_page", "child_page": {"title": "六月"}},
                {"id": "current-may", "type": "child_page", "child_page": {"title": "五月"}},
            ],
            "old-may": [
                {"id": "h-old", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月5日")]}},
                {"id": "old-done", "type": "to_do", "to_do": {"checked": True, "rich_text": [_rt("旧任务")]}},
            ],
            "june": [
                {"id": "h-june", "type": "heading_1", "heading_1": {"rich_text": [_rt("6月1日")]}},
            ],
            "current-may": [
                {"id": "h-current", "type": "heading_1", "heading_1": {"rich_text": [_rt("5月5日")]}},
                {"id": "current-undone", "type": "to_do", "to_do": {"checked": False, "rich_text": [_rt("当前任务")]}},
            ],
        }

    async def search(self, query: str, object_type: str | None = None, page_size: int = 10) -> list[dict]:
        del object_type, page_size
        if query == "HYX的计划通":
            return [{"id": "plan", "object": "page", "properties": {"title": {"title": [_rt("HYX的计划通")]}}}]
        return []


def test_notion_plan_reader_extracts_today_summary_and_important_items() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    reader = NotionPlanReader(
        notion_client=FakePlanClient(),
        root_title="Hypoxanthine's Home",
        plan_title="HYX的计划通",
        semester_title="研一下",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 3
    assert summary.done_count == 1
    assert summary.completion_rate == "1/3"
    assert [item.title for item in summary.done_items] == ["完成论文"]
    assert [item.title for item in summary.undone_items] == ["准备组会", "红色提醒"]
    assert [item.title for item in summary.important_items] == ["准备组会", "红色提醒"]
    assert "今日计划通完成率：1/3" in summary.to_payload()["human_summary"]
    assert "重要提醒" in summary.to_payload()["human_summary"]


def test_notion_plan_reader_falls_back_to_direct_plan_page_search_when_root_is_missing() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    reader = NotionPlanReader(
        notion_client=FakePlanClientWithoutRoot(),
        root_title="Hypoxanthine's Home",
        plan_title="HYX的计划通",
        semester_title="研一下",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 3
    assert summary.completion_rate == "1/3"


def test_notion_plan_reader_uses_configured_plan_page_id_without_search() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    client = FakePlanClientWithConfiguredPageId()
    reader = NotionPlanReader(
        notion_client=client,
        plan_page_id="configured-plan",
        root_title="",
        plan_title="",
        semester_title="",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 3
    assert client.search_calls == []


def test_notion_plan_reader_discovers_nested_page_containing_today_heading() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    reader = NotionPlanReader(
        notion_client=FakePlanClientWithDiscoverableMonth(),
        plan_page_id="plan",
        root_title="",
        plan_title="",
        semester_title="",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 1
    assert summary.undone_items[0].title == "无硬编码发现"


def test_notion_plan_reader_prefers_latest_matching_month_page_when_month_repeats() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    reader = NotionPlanReader(
        notion_client=FakePlanClientWithRepeatedMonthPages(),
        plan_page_id="plan",
        root_title="",
        plan_title="",
        semester_title="",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 1
    assert summary.undone_items[0].title == "当前任务"


def test_notion_plan_reader_supports_semester_heading_with_chinese_month_page() -> None:
    from hypo_agent.core.notion_plan import NotionPlanReader

    reader = NotionPlanReader(
        notion_client=FakePlanClientWithSemesterHeading(),
        root_title="Hypoxanthine's Home",
        plan_title="HYX的计划通",
        semester_title="研一下",
    )

    summary = asyncio.run(reader.read_today(today=date(2026, 5, 5)))

    assert summary.total == 3
    assert summary.completion_rate == "1/3"
