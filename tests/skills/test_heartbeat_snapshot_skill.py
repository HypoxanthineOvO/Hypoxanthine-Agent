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
                        "title": [{"type": "text", "plain_text": "三天内任务"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-07"}},
                    "已完成": {"type": "checkbox", "checkbox": False},
                    "优先级": {"type": "select", "select": {"name": "中"}},
                },
            },
            {
                "id": "p3",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "今日已完成"}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-05"}},
                    "已完成": {"type": "checkbox", "checkbox": True},
                    "优先级": {"type": "select", "select": {"name": "高"}},
                },
            },
        ]


async def _fake_system_snapshot() -> dict:
    return {
        "host": "devbox",
        "projects_by_user": [
            {"account": "heyx", "display_name": "贺云翔", "top_processes": ["pytest -q"]}
        ],
    }


def test_heartbeat_snapshot_skill_exposes_expected_tools() -> None:
    skill = HeartbeatSnapshotSkill(system_snapshot_provider=_fake_system_snapshot)

    tool_names = [tool["function"]["name"] for tool in skill.tools]

    assert tool_names == [
        "get_system_snapshot",
        "get_mail_snapshot",
        "get_notion_todo_snapshot",
        "get_reminder_snapshot",
        "get_heartbeat_snapshot",
    ]


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
    assert payload["system"]["host"] == "devbox"
    assert payload["mail"]["counts"] == {
        "important": 1,
        "low_priority": 1,
        "archive": 0,
        "system": 0,
        "failed": 0,
    }
    assert payload["mail"]["important"][0]["attachments"] == ["a.pdf"]
    assert payload["notion_todo"]["pending_today"][0]["title"] == "今天高优任务"
    assert payload["notion_todo"]["high_priority_due_soon"][0]["title"] == "今天高优任务"
    assert payload["notion_todo"]["completed_today"][0]["title"] == "今日已完成"
    assert payload["reminders"]["overdue"][0]["title"] == "过期提醒"
    assert payload["reminders"]["due_soon"][0]["title"] == "半天内提醒"


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

