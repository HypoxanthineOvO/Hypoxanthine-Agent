from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from hypo_agent.skills.info_reach_skill import InfoReachSkill


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


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")])
def test_info_query_returns_friendly_error_on_http_failures(tmp_path: Path, exc: Exception) -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(exc))
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    output = asyncio.run(skill.execute("info_query", {"time_range": "today"}))

    assert output.status == "error"
    assert "Hypo-Info" in output.error_info


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
