from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import hypo_agent.skills.notion_skill as notion_skill_module
from hypo_agent.channels.notion.notion_client import NotionUnavailableError
from hypo_agent.skills.notion_skill import NotionSkill


class FakeNotionClient:
    def __init__(self) -> None:
        self.database_id = "22222222-2222-2222-2222-222222222222"
        self.page_payload = {
            "id": "11111111-1111-1111-1111-111111111111",
            "parent": {"type": "database_id", "database_id": self.database_id},
            "properties": {
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [{"type": "text", "plain_text": "开发计划", "annotations": {}}],
                },
                "Status": {"id": "status", "type": "status", "status": {"name": "Done"}},
                "Tags": {
                    "id": "tags",
                    "type": "multi_select",
                    "multi_select": [{"name": "AI"}, {"name": "Agent"}],
                },
            },
        }
        self.page_blocks = [
            {
                "id": "block-1",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "plain_text": "里程碑", "annotations": {}}]},
            },
            {
                "id": "block-2",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "plain_text": "完成 NotionSkill", "annotations": {}}]
                },
            },
        ]
        self.append_calls: list[tuple[str, list[dict]]] = []
        self.deleted_blocks: list[str] = []
        self.update_calls: list[tuple[str, dict]] = []
        self.query_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.database_payload = {
            "id": self.database_id,
            "title": [{"type": "text", "plain_text": "待办数据库", "annotations": {}}],
            "properties": {
                "Name": {"id": "title", "type": "title"},
                "Status": {"id": "status", "type": "status"},
                "Tags": {"id": "tags", "type": "multi_select"},
                "Estimate": {"id": "est", "type": "number"},
                "Done": {"id": "done", "type": "checkbox"},
            },
        }

    async def get_page(self, page_id: str) -> dict:
        assert page_id
        if page_id == "parent-1":
            return {
                "id": "parent-1",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "论文返修", "annotations": {}}],
                    }
                },
            }
        return dict(self.page_payload)

    async def get_page_content(self, page_id: str) -> list[dict]:
        assert page_id
        return list(self.page_blocks)

    async def append_blocks(self, page_id: str, blocks: list[dict]) -> None:
        self.append_calls.append((page_id, blocks))

    async def delete_block(self, block_id: str) -> None:
        self.deleted_blocks.append(block_id)

    async def update_page_properties(self, page_id: str, properties: dict) -> dict:
        self.update_calls.append((page_id, properties))
        return {"id": page_id, "properties": properties}

    async def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 50,
    ) -> list[dict]:
        self.query_calls.append(
            {
                "database_id": database_id,
                "filter": filter,
                "sorts": sorts,
                "page_size": page_size,
            }
        )
        return [
            {
                "id": "page-1",
                "last_edited_time": "2026-03-27T10:00:00.000Z",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "完成测试", "annotations": {}}],
                    },
                    "日期": {"type": "date", "date": {"start": "2026-04-03"}},
                    "已完成": {"type": "checkbox", "checkbox": False},
                    "Status": {"type": "status", "status": {"name": "In Progress"}},
                    "Tags": {
                        "type": "multi_select",
                        "multi_select": [{"name": "QA"}, {"name": "Notion"}],
                    },
                },
            }
        ]

    async def create_page(
        self,
        parent: dict,
        properties: dict,
        children: list[dict] | None = None,
    ) -> dict:
        self.create_calls.append(
            {"parent": parent, "properties": properties, "children": children}
        )
        return {
            "id": "new-page",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"type": "text", "plain_text": "新条目", "annotations": {}}],
                }
            },
        }

    async def search(
        self,
        query: str,
        object_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict]:
        self.search_calls.append(
            {"query": query, "object_type": object_type, "page_size": page_size}
        )
        return [
            {
                "object": object_type or "page",
                "id": "result-1",
                "last_edited_time": "2026-03-27T11:00:00.000Z",
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Hypo-Agent 开发日志", "annotations": {}}],
                    }
                },
            }
        ]

    async def get_database(self, database_id: str) -> dict:
        assert database_id
        return dict(self.database_payload)


class DummyHeartbeatService:
    def __init__(self) -> None:
        self.registrations: list[tuple[str, object]] = []

    def register_event_source(self, name: str, callback: object) -> None:
        self.registrations.append((name, callback))


def test_build_client_from_config_passes_timeout_settings(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RecordingNotionClient:
        def __init__(self, integration_secret: str, **kwargs) -> None:
            captured["integration_secret"] = integration_secret
            captured.update(kwargs)

    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    default_workspace: "Hypo"
    todo_database_id: "todo-db"
    proxy_url: "http://127.0.0.1:7890"
    timeout_ms: 60000
    api_timeout_seconds: 30
    max_retries: 4
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(notion_skill_module, "NotionClient", RecordingNotionClient)

    NotionSkill(secrets_path=secrets_path)

    assert captured == {
        "integration_secret": "secret_xxx",
        "proxy_url": "http://127.0.0.1:7890",
        "timeout_ms": 60000,
        "api_timeout_seconds": 30.0,
        "max_retries": 4,
    }


def test_notion_skill_close_closes_underlying_client() -> None:
    class ClosableFakeNotionClient(FakeNotionClient):
        def __init__(self) -> None:
            super().__init__()
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    async def _run() -> None:
        client = ClosableFakeNotionClient()
        skill = NotionSkill(notion_client=client)
        await skill.close()
        assert client.closed == 1

    asyncio.run(_run())


def test_read_page_formats_title_properties_and_markdown() -> None:
    async def _run() -> None:
        skill = NotionSkill(notion_client=FakeNotionClient())

        result = await skill.execute(
            "notion_read_page",
            {"page_id": "https://www.notion.so/workspace/dev-plan-11111111111111111111111111111111"},
        )

        assert result.status == "success"
        assert "📄 开发计划" in result.result
        assert "Status=Done" in result.result
        assert "Tags=AI, Agent" in result.result
        assert "## 里程碑" in result.result
        assert "完成 NotionSkill" in result.result

    asyncio.run(_run())


def test_export_page_markdown_returns_md_attachment(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = NotionSkill(notion_client=FakeNotionClient(), exports_dir=tmp_path / "exports")

        result = await skill.execute(
            "notion_export_page_markdown",
            {
                "page_id": "11111111111111111111111111111111",
                "filename": "dev-plan",
            },
        )

        assert result.status == "success"
        assert result.attachments
        attachment = result.attachments[0]
        assert attachment.type == "file"
        assert attachment.mime_type == "text/markdown"
        assert attachment.filename is not None
        assert attachment.filename.endswith(".md")
        exported = Path(str(result.result))
        assert exported.exists() is True
        assert exported.read_text(encoding="utf-8").startswith("📄 开发计划")

    asyncio.run(_run())


def test_write_page_append_calls_append_blocks() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_write_page",
            {"page_id": "11111111111111111111111111111111", "content": "# Added", "mode": "append"},
        )

        assert result.status == "success"
        assert client.append_calls
        assert client.append_calls[0][0] == "11111111-1111-1111-1111-111111111111"
        assert "已写入" in result.result

    asyncio.run(_run())


def test_write_page_replace_deletes_supported_blocks_before_append() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        client.page_blocks = [
            {
                "id": "paragraph-1",
                "type": "paragraph",
                "paragraph": {"rich_text": []},
            },
            {
                "id": "child-page-1",
                "type": "child_page",
                "child_page": {"title": "子页面"},
            },
        ]
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_write_page",
            {"page_id": "11111111111111111111111111111111", "content": "new content", "mode": "replace"},
        )

        assert result.status == "success"
        assert client.deleted_blocks == ["paragraph-1"]
        assert client.append_calls

    asyncio.run(_run())


def test_update_page_uses_database_schema_to_build_properties() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_update_page",
            {
                "page_id": "11111111111111111111111111111111",
                "properties": '{"Status":"Done","Tags":["AI"],"Estimate":3,"Done":true}',
            },
        )

        assert result.status == "success"
        assert client.update_calls
        props = client.update_calls[0][1]
        assert props["Status"] == {"status": {"name": "Done"}}
        assert props["Tags"] == {"multi_select": [{"name": "AI"}]}
        assert props["Estimate"] == {"number": 3}
        assert props["Done"] == {"checkbox": True}

    asyncio.run(_run())


def test_update_page_normalizes_schema_aliases_and_notion_style_date_payload() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        client.page_payload["properties"] = {
            "名称": {"id": "title", "type": "title", "title": []},
            "日期": {"id": "date", "type": "date", "date": {"start": "2026-04-20"}},
            "已完成": {"id": "done", "type": "checkbox", "checkbox": False},
        }
        client.database_payload["properties"] = {
            "名称": {"id": "title", "type": "title"},
            "日期": {"id": "date", "type": "date"},
            "已完成": {"id": "done", "type": "checkbox"},
        }
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_update_page",
            {
                "page_id": "11111111111111111111111111111111",
                "properties": '{"Name":"新条目","Due Date":{"date":{"start":"2026-04-21T17:00:00+08:00"}},"Done":true}',
            },
        )

        assert result.status == "success"
        props = client.update_calls[0][1]
        assert props["名称"]["title"][0]["text"]["content"] == "新条目"
        assert props["日期"] == {"date": {"start": "2026-04-21T17:00:00+08:00"}}
        assert props["已完成"] == {"checkbox": True}

    asyncio.run(_run())


def test_query_db_formats_table() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_query_db",
            {"database_id": "22222222222222222222222222222222", "limit": 20},
        )

        assert result.status == "success"
        assert "📊 待办数据库（共 1 条）" in result.result
        assert "| 标题 | Status | Tags | 更新时间 |" in result.result
        assert "| 完成测试 | In Progress | QA, Notion | 2026-03-27 |" in result.result

    asyncio.run(_run())


def test_query_db_normalizes_filter_and_sort_property_aliases() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        client.database_payload["properties"] = {
            "名称": {"id": "title", "type": "title"},
            "日期": {"id": "date", "type": "date"},
            "已完成": {"id": "done", "type": "checkbox"},
        }
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_query_db",
            {
                "database_id": "22222222222222222222222222222222",
                "filter": '{"or":[{"property":"Name","title":{"contains":"找娄老师"}},{"property":"Due Date","date":{"on_or_before":"2026-04-22"}}]}',
                "sorts": '[{"property":"Due Date","direction":"ascending"}]',
                "limit": 20,
            },
        )

        assert result.status == "success"
        assert client.query_calls
        assert client.query_calls[0]["filter"] == {
            "or": [
                {"property": "名称", "title": {"contains": "找娄老师"}},
                {"property": "日期", "date": {"on_or_before": "2026-04-22"}},
            ]
        }
        assert client.query_calls[0]["sorts"] == [{"property": "日期", "direction": "ascending"}]

    asyncio.run(_run())


def test_query_db_normalizes_time_sort_alias_to_date_property() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        client.database_payload["properties"] = {
            "名称": {"id": "title", "type": "title"},
            "日期": {"id": "date", "type": "date"},
        }
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_query_db",
            {
                "database_id": "22222222222222222222222222222222",
                "sorts": '[{"property":"时间","direction":"ascending"}]',
                "limit": 20,
            },
        )

        assert result.status == "success"
        assert client.query_calls
        assert client.query_calls[0]["sorts"] == [{"property": "日期", "direction": "ascending"}]

    asyncio.run(_run())


def test_query_db_normalizes_generic_date_aliases_to_only_date_property() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        client.database_payload["properties"] = {
            "Task": {"id": "title", "type": "title"},
            "Date": {"id": "when", "type": "date"},
        }
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_query_db",
            {
                "database_id": "22222222222222222222222222222222",
                "filter": '{"property":"日期","date":{"on_or_after":"2026-04-22"}}',
                "sorts": '[{"property":"时间","direction":"ascending"}]',
                "limit": 20,
            },
        )

        assert result.status == "success"
        assert client.query_calls
        assert client.query_calls[0]["filter"] == {
            "property": "Date",
            "date": {"on_or_after": "2026-04-22"},
        }
        assert client.query_calls[0]["sorts"] == [{"property": "Date", "direction": "ascending"}]

    asyncio.run(_run())


def test_create_entry_constructs_parent_properties_and_children() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_create_entry",
            {
                "database_id": "22222222222222222222222222222222",
                "properties": '{"Name":"新条目","Status":"In Progress","Tags":["AI"],"Estimate":5}',
                "content": "hello",
            },
        )

        assert result.status == "success"
        payload = client.create_calls[0]
        assert payload["parent"] == {"database_id": "22222222-2222-2222-2222-222222222222"}
        assert payload["properties"]["Name"]["title"][0]["text"]["content"] == "新条目"
        assert payload["properties"]["Status"] == {"status": {"name": "In Progress"}}
        assert payload["children"]
        assert "已创建条目：新条目" in result.result

    asyncio.run(_run())


def test_create_entry_rejects_unknown_property_before_remote_api_call() -> None:
    async def _run() -> None:
        client = FakeNotionClient()
        skill = NotionSkill(notion_client=client)

        result = await skill.execute(
            "notion_create_entry",
            {
                "database_id": "22222222222222222222222222222222",
                "properties": '{"Wrong Status":"In Progress"}',
            },
        )

        assert result.status == "error"
        assert "未知 Notion 字段" in result.error_info
        assert "Wrong Status" in result.error_info
        assert "notion_get_schema" in result.error_info
        assert client.create_calls == []

    asyncio.run(_run())


def test_search_formats_results() -> None:
    async def _run() -> None:
        skill = NotionSkill(notion_client=FakeNotionClient())

        result = await skill.execute(
            "notion_search",
            {"query": "开发日志", "type": "page"},
        )

        assert result.status == "success"
        assert "🔍 搜索结果（共 1 条）" in result.result
        assert "Hypo-Agent 开发日志" in result.result
        assert "最后编辑: 2026-03-27" in result.result

    asyncio.run(_run())


def test_notion_unavailable_returns_friendly_message() -> None:
    class UnavailableClient(FakeNotionClient):
        async def search(
            self,
            query: str,
            object_type: str | None = None,
            page_size: int = 10,
        ) -> list[dict]:
            raise NotionUnavailableError("boom")

    async def _run() -> None:
        skill = NotionSkill(notion_client=UnavailableClient())

        result = await skill.execute("notion_search", {"query": "x"})

        assert result.status == "error"
        assert "Notion 当前不可用" in result.error_info

    asyncio.run(_run())


def test_query_db_api_error() -> None:
    class ApiErrorClient(FakeNotionClient):
        async def get_database(self, database_id: str) -> dict:
            del database_id
            raise NotionUnavailableError("Notion query database 失败：ValidationError - body.filter.or should be defined")

    async def _run() -> None:
        skill = NotionSkill(notion_client=ApiErrorClient())

        result = await skill.execute(
            "notion_query_db",
            {"database_id": "22222222222222222222222222222222", "filter": '{"or": []}', "limit": 20},
        )

        assert result.status == "error"
        assert "body.filter.or should be defined" in result.error_info

    asyncio.run(_run())


def test_query_db_timeout() -> None:
    class TimeoutClient(FakeNotionClient):
        async def query_database(
            self,
            database_id: str,
            filter: dict | None = None,
            sorts: list | None = None,
            page_size: int = 50,
        ) -> list[dict]:
            del database_id, filter, sorts, page_size
            raise NotionUnavailableError("Notion query database 超时（10秒）")

    async def _run() -> None:
        skill = NotionSkill(notion_client=TimeoutClient())

        result = await skill.execute(
            "notion_query_db",
            {"database_id": "22222222222222222222222222222222", "limit": 20},
        )

        assert result.status == "error"
        assert "超时" in result.error_info

    asyncio.run(_run())


def test_notion_registers_todo_heartbeat_source_when_configured(tmp_path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    todo_database_id: "22222222-2222-2222-2222-222222222222"
""".strip(),
            encoding="utf-8",
        )
        heartbeat_service = DummyHeartbeatService()
        client = FakeNotionClient()
        skill = NotionSkill(
            secrets_path=secrets_path,
            notion_client=client,
            heartbeat_service=heartbeat_service,
            now_fn=lambda: datetime(2026, 4, 3, 9, 0, 0, tzinfo=UTC),
        )

        assert skill.name == "notion"
        assert heartbeat_service.registrations[0][0] == "notion_todo"

        payload = await heartbeat_service.registrations[0][1]()

        assert payload == {"items": [{"title": "完成测试（今日相关）"}]}
        assert client.query_calls[0]["database_id"] == "22222222-2222-2222-2222-222222222222"
        assert client.query_calls[0]["page_size"] == 50

    asyncio.run(_run())


def test_notion_heartbeat_source_includes_spanning_today_subtask_with_parent_title(tmp_path: Path) -> None:
    class TodoClient(FakeNotionClient):
        async def query_database(
            self,
            database_id: str,
            filter: dict | None = None,
            sorts: list | None = None,
            page_size: int = 50,
        ) -> list[dict]:
            self.query_calls.append(
                {
                    "database_id": database_id,
                    "filter": filter,
                    "sorts": sorts,
                    "page_size": page_size,
                }
            )
            return [
                {
                    "id": "child-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"type": "text", "plain_text": "拆分任务", "annotations": {}}],
                        },
                        "日期": {
                            "type": "date",
                            "date": {"start": "2026-04-04", "end": "2026-04-06"},
                        },
                        "已完成": {"type": "checkbox", "checkbox": False},
                        "Parent item": {"type": "relation", "relation": [{"id": "parent-1"}]},
                    },
                }
            ]

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    todo_database_id: "22222222-2222-2222-2222-222222222222"
""".strip(),
            encoding="utf-8",
        )
        heartbeat_service = DummyHeartbeatService()
        client = TodoClient()
        NotionSkill(
            secrets_path=secrets_path,
            notion_client=client,
            heartbeat_service=heartbeat_service,
            now_fn=lambda: datetime(2026, 4, 5, 9, 0, 0, tzinfo=UTC),
        )

        payload = await heartbeat_service.registrations[0][1]()

        assert payload == {"items": [{"title": "论文返修 / 拆分任务（今日相关）"}]}
        assert client.query_calls[0]["filter"] is None

    asyncio.run(_run())


def test_todo_item_matches_today_supports_cover_today_and_due_only() -> None:
    skill = NotionSkill(notion_client=FakeNotionClient())
    item = {
        "title": "拆分任务",
        "date_start": "2026-04-04",
        "date_end": "2026-04-06",
        "is_date_span": True,
    }

    assert skill.todo_item_matches_today(
        item,
        today=date(2026, 4, 5),
        match_mode="cover_today",
    ) is True
    assert skill.todo_item_matches_today(
        item,
        today=date(2026, 4, 5),
        match_mode="due_only",
    ) is False


def test_get_todo_snapshot_hydrates_parent_titles_and_preserves_daily_recurrence_metadata(tmp_path: Path) -> None:
    class TodoClient(FakeNotionClient):
        async def query_database(
            self,
            database_id: str,
            filter: dict | None = None,
            sorts: list | None = None,
            page_size: int = 50,
        ) -> list[dict]:
            self.query_calls.append(
                {
                    "database_id": database_id,
                    "filter": filter,
                    "sorts": sorts,
                    "page_size": page_size,
                }
            )
            return [
                {
                    "id": "child-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"type": "text", "plain_text": "整理实验记录", "annotations": {}}],
                        },
                        "日期": {"type": "date", "date": {"start": "2026-04-05"}},
                        "已完成": {"type": "checkbox", "checkbox": False},
                        "优先级": {"type": "select", "select": {"name": "高"}},
                        "Parent item": {"type": "relation", "relation": [{"id": "parent-1"}]},
                    },
                },
                {
                    "id": "daily-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"type": "text", "plain_text": "姜黄素", "annotations": {}}],
                        },
                        "日期": {"type": "date", "date": {"start": "2026-04-06"}},
                        "已完成": {"type": "checkbox", "checkbox": False},
                        "优先级": {"type": "select", "select": {"name": "高"}},
                        "重复": {
                            "type": "rich_text",
                            "rich_text": [{"type": "text", "plain_text": "每天", "annotations": {}}],
                        },
                    },
                },
            ]

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    todo_database_id: "22222222-2222-2222-2222-222222222222"
""".strip(),
            encoding="utf-8",
        )
        client = TodoClient()
        skill = NotionSkill(secrets_path=secrets_path, notion_client=client)

        result = await skill.get_todo_snapshot()

        assert result["available"] is True
        assert result["database_id"] == "22222222-2222-2222-2222-222222222222"
        assert result["items"][0]["title"] == "整理实验记录"
        assert result["items"][0]["parent_title"] == "论文返修"
        assert result["items"][1]["recurrence"] == "每天"

    asyncio.run(_run())


def test_get_todo_snapshot_normalizes_date_range_and_display_title(tmp_path: Path) -> None:
    class TodoClient(FakeNotionClient):
        async def query_database(
            self,
            database_id: str,
            filter: dict | None = None,
            sorts: list | None = None,
            page_size: int = 50,
        ) -> list[dict]:
            self.query_calls.append(
                {
                    "database_id": database_id,
                    "filter": filter,
                    "sorts": sorts,
                    "page_size": page_size,
                }
            )
            return [
                {
                    "id": "child-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"type": "text", "plain_text": "整理实验记录", "annotations": {}}],
                        },
                        "日期": {
                            "type": "date",
                            "date": {"start": "2026-04-04", "end": "2026-04-06"},
                        },
                        "已完成": {"type": "checkbox", "checkbox": False},
                        "Parent item": {"type": "relation", "relation": [{"id": "parent-1"}]},
                    },
                }
            ]

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    todo_database_id: "22222222-2222-2222-2222-222222222222"
""".strip(),
            encoding="utf-8",
        )
        skill = NotionSkill(secrets_path=secrets_path, notion_client=TodoClient())

        result = await skill.get_todo_snapshot()

        assert result["available"] is True
        item = result["items"][0]
        assert item["date_start"] == "2026-04-04"
        assert item["date_end"] == "2026-04-06"
        assert item["is_date_span"] is True
        assert item["display_title"] == "论文返修 / 整理实验记录"

    asyncio.run(_run())


def test_get_todo_snapshot_returns_unavailable_payload_when_query_fails(tmp_path: Path) -> None:
    class FailingTodoClient(FakeNotionClient):
        async def query_database(
            self,
            database_id: str,
            filter: dict | None = None,
            sorts: list | None = None,
            page_size: int = 50,
        ) -> list[dict]:
            del database_id, filter, sorts, page_size
            raise NotionUnavailableError("Notion query database 失败：boom")

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    todo_database_id: "22222222-2222-2222-2222-222222222222"
""".strip(),
            encoding="utf-8",
        )
        skill = NotionSkill(secrets_path=secrets_path, notion_client=FailingTodoClient())

        result = await skill.get_todo_snapshot()

        assert result["available"] is False
        assert "boom" in result["error"]
        assert "查询失败" in result["human_summary"]

    asyncio.run(_run())


def test_build_client_from_config_passes_proxy_url(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class RecordingNotionClient:
        def __init__(self, integration_secret: str, **kwargs) -> None:
            captured["integration_secret"] = integration_secret
            captured["proxy_url"] = str(kwargs.get("proxy_url") or "")

    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        """
providers: {}
services:
  notion:
    integration_secret: "secret_xxx"
    proxy_url: "http://127.0.0.1:7890"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("hypo_agent.skills.notion_skill.NotionClient", RecordingNotionClient)

    NotionSkill(secrets_path=secrets_path)

    assert captured["integration_secret"] == "secret_xxx"
    assert captured["proxy_url"] == "http://127.0.0.1:7890"
