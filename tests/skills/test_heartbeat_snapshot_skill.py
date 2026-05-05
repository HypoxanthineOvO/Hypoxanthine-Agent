from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.models import SkillOutput
from hypo_agent.skills.heartbeat_snapshot_skill import HeartbeatSnapshotSkill


class StubEmailSkill:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def scan_emails(self, *, params=None) -> dict:
        payload = dict(params or {})
        self.calls.append(payload)
        return {
            "new_emails": 2,
            "summary": "mail ok",
            "items": [
                {
                    "message_id": "<1>",
                    "category": "important",
                    "from": "boss@example.com",
                    "subject": "紧急",
                    "summary": "需要尽快处理",
                    "attachment_paths": ["memory/email_attachments/a.pdf"],
                },
                {
                    "message_id": "<2>",
                    "category": "low_priority",
                    "from": "notice@example.com",
                    "subject": "通知",
                    "summary": "普通通知",
                },
            ],
        }


class StubReminderSkill:
    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        assert tool_name == "list_reminders"
        assert params == {"status": "all"}
        return SkillOutput(
            status="success",
            result={
                "items": [
                    {
                        "id": 1,
                        "title": "过期提醒",
                        "status": "active",
                        "next_run_at": "2026-04-05T08:00:00+08:00",
                    },
                    {
                        "id": 2,
                        "title": "半天内提醒",
                        "status": "active",
                        "next_run_at": "2026-04-05T20:00:00+08:00",
                    },
                    {
                        "id": 3,
                        "title": "已完成提醒",
                        "status": "completed",
                        "next_run_at": "2026-04-06T08:00:00+08:00",
                    },
                ]
            },
        )


class StubNotionSkill:
    def __init__(self) -> None:
        self._todo_database_id = "todo-db"
        self._client = self

    async def get_todo_snapshot(
        self,
        *,
        structured_store=None,
        limit: int = 50,
    ) -> dict:
        del structured_store, limit
        return {
            "available": True,
            "database_id": "todo-db",
            "items": [
                {
                    "id": "p1",
                    "title": "今天高优任务",
                    "due_date": "2026-04-05",
                    "done": False,
                    "priority": "高",
                    "tags": "",
                    "status": "",
                    "recurrence": "",
                    "parent_page_id": "",
                    "parent_title": "",
                },
                {
                    "id": "p2",
                    "title": "姜黄素",
                    "due_date": "2026-04-06",
                    "done": False,
                    "priority": "高",
                    "tags": "",
                    "status": "",
                    "recurrence": "每天",
                    "parent_page_id": "",
                    "parent_title": "",
                },
                {
                    "id": "p3",
                    "title": "姜黄素",
                    "due_date": "2026-04-05",
                    "done": True,
                    "priority": "高",
                    "tags": "",
                    "status": "",
                    "recurrence": "每天",
                    "parent_page_id": "",
                    "parent_title": "",
                },
                {
                    "id": "p4",
                    "title": "整理实验记录",
                    "due_date": "2026-04-04",
                    "date_start": "2026-04-04",
                    "date_end": "2026-04-06",
                    "is_date_span": True,
                    "done": False,
                    "priority": "高",
                    "tags": "",
                    "status": "",
                    "recurrence": "",
                    "parent_page_id": "parent-1",
                    "parent_title": "论文返修",
                    "display_title": "论文返修 / 整理实验记录",
                },
            ],
        }

    async def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 50,
    ) -> list[dict]:
        assert database_id == "todo-db"
        assert filter is None
        assert sorts is None
        assert page_size == 50
        return [
            {
                "id": "p1",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "今天高优任务"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-05"}},
                    "已完成": {"type": "checkbox", "checkbox": False},
                    "优先级": {"type": "select", "select": {"name": "高"}},
                },
            },
            {
                "id": "p2",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "姜黄素"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-06"}},
                    "已完成": {"type": "checkbox", "checkbox": False},
                    "优先级": {"type": "select", "select": {"name": "高"}},
                    "重复": {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": "每天"}]},
                },
            },
            {
                "id": "p3",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "姜黄素"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-05"}},
                    "已完成": {"type": "checkbox", "checkbox": True},
                    "优先级": {"type": "select", "select": {"name": "高"}},
                    "重复": {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": "每天"}]},
                },
            },
            {
                "id": "p4",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "整理实验记录"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-05"}},
                    "已完成": {"type": "checkbox", "checkbox": False},
                    "优先级": {"type": "select", "select": {"name": "高"}},
                    "Parent item": {
                        "type": "relation",
                        "relation": [{"id": "parent-1"}],
                    },
                },
            },
        ]

    async def get_page(self, page_id: str) -> dict:
        assert page_id == "parent-1"
        return {
            "id": "parent-1",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"type": "text", "plain_text": "论文返修"}],
                }
            },
        }


class DiscoverableNotionSkill:
    def __init__(self) -> None:
        self._todo_database_id = ""
        self._client = self
        self.search_calls: list[dict[str, object]] = []

    async def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 50,
    ) -> list[dict]:
        assert database_id == "todo-db-discovered"
        assert filter is None
        assert sorts is None
        assert page_size == 50
        return []

    async def search(
        self,
        query: str,
        *,
        object_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict]:
        self.search_calls.append(
            {
                "query": query,
                "object_type": object_type,
                "page_size": page_size,
            }
        )
        return [
            {
                "id": "todo-db-discovered",
                "object": "database",
                "url": "https://www.notion.so/todo-db-discovered",
                "last_edited_time": "2026-04-07T12:00:00.000Z",
                "title": [{"type": "text", "plain_text": "HYX的计划通"}],
            }
        ]


class DiscoverableNotionSkillWithSpacedTitle(DiscoverableNotionSkill):
    async def search(
        self,
        query: str,
        *,
        object_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict]:
        self.search_calls.append(
            {
                "query": query,
                "object_type": object_type,
                "page_size": page_size,
            }
        )
        return [
            {
                "id": "todo-db-spaced",
                "object": "database",
                "url": "https://www.notion.so/todo-db-spaced",
                "last_edited_time": "2026-04-07T12:00:00.000Z",
                "title": [{"type": "text", "plain_text": "HYX 的计划通"}],
            }
        ]


class StubPreferenceStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_preference(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value


async def _fake_system_snapshot() -> dict:
    return {
        "host": "devbox",
        "projects_by_user": [
            {
                "account": "heyx",
                "display_name": "贺云翔",
                "process_count": 2,
                "top_processes": ["pytest -q", "python train.py"],
                "top_process_details": [
                    {
                        "pid": 123,
                        "cpu_percent": 80.0,
                        "memory_percent": 1.2,
                        "gpu_memory_mb": 4096,
                        "gpu_cards": ["0"],
                        "command": "python train.py --project fast-ridge",
                    }
                ],
                "activity_summary": "贺云翔/heyx：2 个进程，CPU 80.0%，内存 1.2%，GPU 4096 MiB（0）；主要进程：python train.py --project fast-ridge；pytest -q",
            }
        ],
        "project_activity_summary": [
            "贺云翔/heyx：2 个进程，CPU 80.0%，内存 1.2%，GPU 4096 MiB（0）；主要进程：python train.py --project fast-ridge；pytest -q"
        ],
        "top_system_processes": [
            {
                "user": "heyx",
                "display_name": "贺云翔",
                "pid": 123,
                "cpu_percent": 80.0,
                "memory_percent": 1.2,
                "gpu_memory_mb": 4096,
                "gpu_cards": ["0"],
                "command": "python train.py --project fast-ridge",
            }
        ],
    }


def test_heartbeat_snapshot_skill_exposes_expected_tools() -> None:
    skill = HeartbeatSnapshotSkill(system_snapshot_provider=_fake_system_snapshot)

    tool_names = [tool["function"]["name"] for tool in skill.tools]

    assert tool_names == [
        "get_mail_snapshot",
        "get_notion_todo_snapshot",
        "get_reminder_snapshot",
        "get_heartbeat_snapshot",
    ]


def test_heartbeat_snapshot_prefers_plan_snapshot_when_available() -> None:
    class PlanNotionSkill:
        async def get_plan_snapshot(self) -> dict:
            return {
                "available": True,
                "completion_rate": "1/2",
                "done_items": [{"title": "完成论文"}],
                "undone_items": [{"title": "准备组会"}],
                "important_items": [{"title": "准备组会"}],
                "human_summary": "今日计划通完成率：1/2\n\n重要提醒：\n- 准备组会",
            }

    skill = HeartbeatSnapshotSkill(notion_skill=PlanNotionSkill())
    result = asyncio.run(skill.execute("get_notion_todo_snapshot", {}))

    assert result.status == "success"
    assert result.result["source"] == "HYX的计划通"
    assert result.result["pending_today"] == [{"title": "准备组会"}]
    assert result.result["completed_today"] == [{"title": "完成论文"}]
    assert result.result["high_priority_due_soon"] == [{"title": "准备组会"}]


def test_heartbeat_snapshot_falls_back_to_todo_snapshot_when_plan_snapshot_fails() -> None:
    class BrokenPlanNotionSkill(StubNotionSkill):
        async def get_plan_snapshot(self) -> dict:
            raise ValueError("Notion root page not found: Hypoxanthine's Home")

    skill = HeartbeatSnapshotSkill(
        notion_skill=BrokenPlanNotionSkill(),
        now_iso_provider=lambda: "2026-04-05T12:00:00+08:00",
    )

    result = asyncio.run(skill.execute("get_notion_todo_snapshot", {}))

    assert result.status == "success"
    assert result.result["available"] is True
    assert result.result["pending_today"][0]["title"] == "今天高优任务"
    assert "今日相关未完成" in result.result["human_summary"]


def test_heartbeat_snapshot_skill_returns_structured_sections(tmp_path: Path) -> None:
    people_index = tmp_path / "memory" / "people" / "index.md"
    people_index.parent.mkdir(parents=True, exist_ok=True)
    people_index.write_text(
        """
| 账号 | 姓名 |
| --- | --- |
| heyx | 贺云翔 |
""".strip(),
        encoding="utf-8",
    )
    skill = HeartbeatSnapshotSkill(
        email_skill=StubEmailSkill(),
        reminder_skill=StubReminderSkill(),
        notion_skill=StubNotionSkill(),
        system_snapshot_provider=_fake_system_snapshot,
        people_index_path=people_index,
        now_iso_provider=lambda: "2026-04-05T12:00:00+08:00",
    )

    result = asyncio.run(skill.execute("get_heartbeat_snapshot", {}))

    assert result.status == "success"
    payload = result.result
    assert payload["checked_at"] == "2026-04-05T12:00:00+08:00"
    assert "system" not in payload
    assert payload["mail"]["counts"] == {
        "important": 1,
        "low_priority": 1,
        "archive": 0,
        "system": 0,
        "failed": 0,
    }
    assert "重要邮件" in payload["mail"]["human_summary"]
    assert payload["mail"]["important"][0]["attachments"] == ["a.pdf"]
    assert payload["notion_todo"]["pending_today"][0]["title"] == "今天高优任务"
    assert payload["notion_todo"]["high_priority_due_soon"][0]["title"] == "今天高优任务"
    assert [item["title"] for item in payload["notion_todo"]["high_priority_due_soon"]] == [
        "今天高优任务",
        "整理实验记录",
    ]
    assert payload["notion_todo"]["completed_today"][0]["title"] == "姜黄素"
    assert payload["notion_todo"]["pending_today"][1]["title"] == "整理实验记录"
    assert payload["notion_todo"]["pending_today"][1]["parent_title"] == "论文返修"
    assert payload["notion_todo"]["pending_today"][1]["is_date_span"] is True
    assert "今日相关未完成" in payload["notion_todo"]["human_summary"]
    assert "今日相关未完成：\n\n- 今天高优任务" in payload["notion_todo"]["human_summary"]
    assert "- 论文返修 / 整理实验记录" in payload["notion_todo"]["human_summary"]
    assert "\n\n三天内高优未完成：\n\n- 今天高优任务" in payload["notion_todo"]["human_summary"]
    assert "\n- 论文返修 / 整理实验记录" in payload["notion_todo"]["human_summary"]
    assert payload["reminders"]["overdue"][0]["title"] == "过期提醒"
    assert payload["reminders"]["due_soon"][0]["title"] == "半天内提醒"
    assert "过期提醒" in payload["reminders"]["human_summary"]


def test_heartbeat_snapshot_skill_mail_snapshot_uses_heartbeat_scan_mode() -> None:
    email_skill = StubEmailSkill()
    skill = HeartbeatSnapshotSkill(
        email_skill=email_skill,
        system_snapshot_provider=_fake_system_snapshot,
        now_iso_provider=lambda: "2026-04-05T12:00:00+08:00",
    )

    result = asyncio.run(skill.execute("get_mail_snapshot", {}))

    assert result.status == "success"
    assert email_skill.calls == [{"triggered_by": "heartbeat", "unread_only": True}]
    assert result.result["new_emails"] == 2


def test_heartbeat_snapshot_skill_discovers_notion_todo_candidate_and_requests_confirmation() -> None:
    store = StubPreferenceStore()
    notion_skill = DiscoverableNotionSkill()
    skill = HeartbeatSnapshotSkill(
        notion_skill=notion_skill,
        structured_store=store,
        system_snapshot_provider=_fake_system_snapshot,
        now_iso_provider=lambda: "2026-04-07T12:00:00+08:00",
    )

    result = asyncio.run(skill.execute("get_notion_todo_snapshot", {}))

    assert result.status == "success"
    assert result.result["available"] is False
    assert "确认绑定" in result.result["human_summary"]
    assert "HYX的计划通" in result.result["human_summary"]
    assert notion_skill.search_calls == [
        {"query": "HYX的计划通", "object_type": "database", "page_size": 10}
    ]
    assert store.values["notion.todo_database_candidate_pending"].startswith("{")


def test_heartbeat_snapshot_skill_discovers_notion_todo_candidate_when_title_differs_only_by_spaces() -> None:
    store = StubPreferenceStore()
    notion_skill = DiscoverableNotionSkillWithSpacedTitle()
    skill = HeartbeatSnapshotSkill(
        notion_skill=notion_skill,
        structured_store=store,
        system_snapshot_provider=_fake_system_snapshot,
        now_iso_provider=lambda: "2026-04-07T12:00:00+08:00",
    )

    result = asyncio.run(skill.execute("get_notion_todo_snapshot", {}))

    assert result.status == "success"
    assert result.result["binding_status"] == "pending_confirmation"
    assert "HYX 的计划通" in result.result["human_summary"]
    assert store.values["notion.todo_database_candidate_pending"].startswith("{")
