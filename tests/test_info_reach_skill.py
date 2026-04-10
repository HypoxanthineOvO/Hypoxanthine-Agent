from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from hypo_agent.skills.info_reach_skill import HypoInfoClient, HypoInfoError, InfoReachSkill


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class DummyHeartbeatService:
    def __init__(self) -> None:
        self.registrations: list[tuple[str, Any]] = []

    def register_event_source(self, name: str, callback: Any) -> None:
        self.registrations.append((name, callback))


def _build_skill(
    *,
    tmp_path: Path,
    transport: httpx.MockTransport | None = None,
    heartbeat_service: DummyHeartbeatService | None = None,
    db_path: Path | None = None,
) -> InfoReachSkill:
    queue = DummyQueue()
    skill = InfoReachSkill(
        message_queue=queue,
        heartbeat_service=heartbeat_service,
        db_path=db_path or (tmp_path / "hypo.db"),
        base_url="http://localhost:8200",
        transport=transport,
    )
    return skill


# ---------------------------------------------------------------------------
# Task 2: info_query and info_summary
# ---------------------------------------------------------------------------


def test_info_query_formats_articles_from_hypo_info_api(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "total": 1,
                "articles": [
                    {
                        "id": "a1",
                        "title": "OpenAI 发布新推理能力",
                        "summary": "重点在于推理链路和成本优化。",
                        "category_l1": "AI",
                        "category_l2": "模型",
                        "importance": 8,
                        "tags": ["reasoning"],
                        "sources": ["blog"],
                        "source_name": "OpenAI",
                        "collected_at": "2026-03-30T01:00:00Z",
                        "url": "https://example.com/a1",
                    }
                ],
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    result = asyncio.run(skill.info_query(category="AI", keyword="推理"))

    assert "OpenAI 发布新推理能力" in result
    assert "重要性：8" in result
    assert "来源：OpenAI" in result


def test_info_summary_formats_digest_sections(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "time_range": "today",
                "generated_at": "2026-03-30T01:00:00Z",
                "highlight": "今天 AI 与芯片新闻密集。",
                "sections": [
                    {"category": "AI", "items": ["模型更新", "Agent 工具链"]},
                    {"category": "Infra", "items": ["算力价格波动"]},
                ],
                "stats": {"total_articles": 12},
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    text = asyncio.run(skill.info_summary(time_range="today"))

    assert "今天 AI 与芯片新闻密集" in text
    assert "AI" in text
    assert "Agent 工具链" in text


def test_info_summary_formats_object_items_without_dumping_json(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "highlight": "今天 AI 新闻偏多。",
                "sections": [
                    {
                        "category": "AI",
                        "items": [
                            {
                                "title": "推理模型发布",
                                "summary": "重点在推理吞吐和成本下降。",
                                "source_name": "OpenAI",
                                "url": "https://example.com/query",
                            }
                        ],
                    }
                ],
                "stats": {"total_articles": 1},
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    text = asyncio.run(skill.info_summary(time_range="today"))

    assert "推理模型发布" in text
    assert "重点在推理吞吐和成本下降。" in text
    assert "OpenAI" in text
    assert "https://example.com/query" in text
    assert "{'title':" not in text


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")])
def test_info_query_returns_friendly_error_on_http_failures(tmp_path: Path, exc: Exception) -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(exc))
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    output = asyncio.run(skill.execute("info_query", {"time_range": "today"}))

    assert output.status == "error"
    assert "Hypo-Info" in output.error_info


def test_info_query_returns_friendly_error_on_http_500(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"detail": "boom"})
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    output = asyncio.run(skill.execute("info_query", {"time_range": "today"}))

    assert output.status == "error"
    assert "500" in output.error_info
    assert "Hypo-Info" in output.error_info


def test_hypo_info_client_supports_summary_and_categories(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/summary":
            return httpx.Response(
                200,
                json={
                    "time_range": "today",
                    "generated_at": "2026-03-30T01:00:00Z",
                    "categories": [{"category": "AI", "article_count": 2, "top_articles": []}],
                    "total_articles": 2,
                },
            )
        if request.url.path == "/api/agent/categories":
            return httpx.Response(
                200,
                json={
                    "categories": [
                        {"category_l1": "AI", "subcategories": ["模型"], "article_count": 12}
                    ]
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = HypoInfoClient("http://localhost:8200", transport=httpx.MockTransport(_handler))

    summary = asyncio.run(client.summary(time_range="today", min_importance=7))
    categories = asyncio.run(client.categories())

    assert summary["total_articles"] == 2
    assert categories["categories"][0]["category_l1"] == "AI"


def test_info_reach_skill_loads_base_url_from_secrets_when_not_passed(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  hypo_info:
    base_url: "http://localhost:9200"
""".strip(),
        encoding="utf-8",
    )

    skill = InfoReachSkill(
        db_path=tmp_path / "hypo.db",
        secrets_path=config_dir / "secrets.yaml",
    )

    assert skill._client._base_url == "http://localhost:9200"


def test_info_reach_skill_uses_default_base_url_when_secrets_missing(tmp_path: Path) -> None:
    skill = InfoReachSkill(
        db_path=tmp_path / "hypo.db",
        secrets_path=tmp_path / "missing-secrets.yaml",
    )

    assert skill._client._base_url == "http://localhost:8200"


def test_info_reach_tools_descriptions_mark_proactive_usage(tmp_path: Path) -> None:
    skill = _build_skill(tmp_path=tmp_path)
    tools = {tool["function"]["name"]: tool["function"] for tool in skill.tools}

    assert "natural-language" in tools["info_query"]["description"]
    assert "raw JSON" in tools["info_query"]["description"]
    assert "natural-language" in tools["info_summary"]["description"]
    assert "raw JSON" in tools["info_summary"]["description"]


def test_info_reach_skill_has_proactive_push_docstring() -> None:
    assert "Hypo-Info 主动推送与订阅管理" in (InfoReachSkill.__doc__ or "")


def test_info_portal_and_info_reach_tool_descriptions_are_separated(tmp_path: Path) -> None:
    from hypo_agent.skills.info_portal_skill import InfoPortalSkill

    portal = InfoPortalSkill(info_client=object())
    reach = InfoReachSkill(db_path=tmp_path / "hypo.db")
    portal_tools = {tool["function"]["name"]: tool["function"] for tool in portal.tools}
    reach_tools = {tool["function"]["name"]: tool["function"] for tool in reach.tools}

    assert portal_tools["info_today"]["description"] == "Get today's news digest, optionally filtered by section."
    assert portal_tools["info_search"]["description"] == "Search Hypo-Info articles by keyword."
    assert "natural-language" in reach_tools["info_query"]["description"]
    assert "natural-language" in reach_tools["info_summary"]["description"]


def test_info_query_description_marks_internal_push_usage(tmp_path: Path) -> None:
    skill = InfoReachSkill(db_path=tmp_path / "hypo.db")
    tools = {tool["function"]["name"]: tool["function"] for tool in skill.tools}
    assert "raw JSON" in tools["info_query"]["description"]


def test_info_reach_execute_formats_subscription_results_for_llm(tmp_path: Path) -> None:
    skill = InfoReachSkill(db_path=tmp_path / "hypo.db")

    created = asyncio.run(
        skill.execute(
            "info_subscribe",
            {
                "name": "ai-watch",
                "keywords": ["Agent", "推理"],
                "categories": ["AI"],
                "schedule": "daily",
            },
        )
    )
    listed = asyncio.run(skill.execute("info_list_subscriptions", {}))
    deleted = asyncio.run(skill.execute("info_delete_subscription", {"name": "ai-watch"}))

    assert created.status == "success"
    assert isinstance(created.result, str)
    assert "ai-watch" in created.result
    assert "Agent" in created.result
    assert listed.status == "success"
    assert isinstance(listed.result, str)
    assert "ai-watch" in listed.result
    assert "daily" in listed.result
    assert deleted.status == "success"
    assert isinstance(deleted.result, str)
    assert "ai-watch" in deleted.result


def test_info_reach_execute_returns_rendered_text_for_query_and_summary(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/query":
            return httpx.Response(
                200,
                json={
                    "articles": [
                        {
                            "title": "推理模型发布",
                            "summary": "重点在推理吞吐和成本下降。",
                            "importance": 9,
                            "source_name": "OpenAI",
                            "url": "https://example.com/query",
                        }
                    ]
                },
            )
        if request.url.path == "/api/agent/digest":
            return httpx.Response(
                200,
                json={
                    "highlight": "今天 AI 新闻偏多。",
                    "sections": [{"category": "AI", "items": ["推理模型发布"]}],
                    "stats": {"total_articles": 1},
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    skill = _build_skill(tmp_path=tmp_path, transport=httpx.MockTransport(_handler))

    query_output = asyncio.run(
        skill.execute(
            "info_query",
            {"category": "AI", "keyword": "推理", "time_range": "today"},
        )
    )
    summary_output = asyncio.run(skill.execute("info_summary", {"time_range": "today"}))

    assert query_output.status == "success"
    assert isinstance(query_output.result, str)
    assert "推理模型发布" in query_output.result
    assert query_output.metadata == {"rendered": True}

    assert summary_output.status == "success"
    assert isinstance(summary_output.result, str)
    assert "今天 AI 新闻偏多" in summary_output.result
    assert summary_output.metadata == {"rendered": True}


# ---------------------------------------------------------------------------
# Task 3: subscriptions, migration, heartbeat
# ---------------------------------------------------------------------------


def test_info_subscription_crud_and_heartbeat_registration(tmp_path: Path) -> None:
    heartbeat_service = DummyHeartbeatService()
    skill = _build_skill(tmp_path=tmp_path, heartbeat_service=heartbeat_service)

    created = asyncio.run(
        skill.info_subscribe(
            name="ai-watch",
            keywords=["Agent", "推理"],
            categories=["AI", "Infra"],
            schedule="daily",
        )
    )
    listed = asyncio.run(skill.info_list_subscriptions())
    deleted = asyncio.run(skill.info_delete_subscription(name="ai-watch"))

    assert heartbeat_service.registrations[0][0] == "hypo_info"
    assert created["name"] == "ai-watch"
    assert listed["items"][0]["categories"] == ["AI", "Infra"]
    assert deleted["deleted"] is True


def test_info_subscription_table_auto_migrates_from_trendradar(tmp_path: Path) -> None:
    db_path = tmp_path / "hypo.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE trendradar_subscriptions (
                name TEXT PRIMARY KEY,
                keywords_json TEXT NOT NULL,
                platforms_json TEXT NOT NULL DEFAULT '[]',
                schedule TEXT NOT NULL DEFAULT 'daily',
                last_run TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO trendradar_subscriptions
            VALUES ('ai-watch', '["AI"]', '["weibo"]', 'daily', NULL, '2026-03-30T00:00:00Z', '2026-03-30T00:00:00Z')
            """
        )
        db.commit()

    skill = _build_skill(tmp_path=tmp_path, db_path=db_path)

    listed = asyncio.run(skill.info_list_subscriptions())

    assert listed["items"][0]["name"] == "ai-watch"
    assert "weibo" in listed["items"][0]["categories"]


def test_check_new_info_returns_hypo_info_name(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "total": 1,
                "articles": [
                    {
                        "id": "b1",
                        "title": "NVIDIA H200 降价",
                        "summary": "算力成本持续下降。",
                        "category_l1": "Infra",
                        "category_l2": "芯片",
                        "importance": 8,
                        "tags": [],
                        "sources": [],
                        "source_name": "路透",
                        "collected_at": "2026-03-30T01:00:00Z",
                        "url": "https://example.com/b1",
                    }
                ],
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)
    asyncio.run(
        skill.info_subscribe(name="chip-watch", keywords=["NVIDIA"], categories=["Infra"])
    )

    result = asyncio.run(skill._check_new_info())

    assert result["name"] == "hypo_info"
    assert result["new_items"] == 1
