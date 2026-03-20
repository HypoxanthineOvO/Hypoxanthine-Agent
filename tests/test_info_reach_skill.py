from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import DirectoryWhitelist
from hypo_agent.security.permission_manager import PermissionManager
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


class DummyRouter:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "技术": "技术方向摘要",
            "学术": "学术方向摘要",
            "娱乐": "娱乐方向摘要",
            "财经": "财经方向摘要",
        }
        self.prompts: list[str] = []

    async def call_lightweight_json(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        self.prompts.append(prompt)
        return dict(self.payload)

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "DummyLightweight"


class FailingRouter(DummyRouter):
    async def call_lightweight_json(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del prompt, session_id
        raise RuntimeError("lightweight model unavailable")


def _write_news_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE platforms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                url TEXT DEFAULT '',
                mobile_url TEXT DEFAULT '',
                first_crawl_time TEXT NOT NULL,
                last_crawl_time TEXT NOT NULL,
                crawl_count INTEGER DEFAULT 1
            );
            CREATE TABLE ai_filter_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT NOT NULL
            );
            CREATE TABLE ai_filter_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_item_id INTEGER NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'hotlist',
                tag_id INTEGER NOT NULL,
                relevance_score REAL DEFAULT 0
            );
            """
        )
        db.execute(
            "INSERT INTO platforms(id, name) VALUES (?, ?), (?, ?)",
            ("weibo", "微博", "bilibili-hot-search", "哔哩哔哩热搜"),
        )
        db.execute(
            """
            INSERT INTO news_items(title, platform_id, rank, url, mobile_url, first_crawl_time, last_crawl_time, crawl_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?),
                   (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "阿里云部分产品涨价 34%",
                "weibo",
                3,
                "https://example.com/weibo-ai",
                "",
                "09-00",
                "09-30",
                5,
                "BLG 终结十连败",
                "bilibili-hot-search",
                1,
                "https://example.com/bili-blg",
                "",
                "10-00",
                "10-30",
                4,
            ),
        )
        db.execute("INSERT INTO ai_filter_tags(tag) VALUES (?)", ("大模型与AI产品",))
        db.execute(
            """
            INSERT INTO ai_filter_results(news_item_id, source_type, tag_id, relevance_score)
            VALUES (1, 'hotlist', 1, 0.9)
            """
        )
        db.commit()


def _write_rss_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE rss_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                feed_id TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                summary TEXT,
                author TEXT,
                first_crawl_time TEXT NOT NULL,
                last_crawl_time TEXT NOT NULL,
                crawl_count INTEGER DEFAULT 1
            );
            """
        )
        db.execute(
            """
            INSERT INTO rss_items(title, feed_id, url, published_at, summary, author, first_crawl_time, last_crawl_time, crawl_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Launch an autonomous AI agent with sandboxed execution in 2 lines of code",
                "hacker-news",
                "https://example.com/hn-agent",
                datetime.now(UTC).replace(hour=9, minute=15, second=0, microsecond=0).isoformat(),
                "AI agent and sandbox execution.",
                "someone",
                datetime.now(UTC).replace(hour=9, minute=20, second=0, microsecond=0).isoformat(),
                datetime.now(UTC).replace(hour=9, minute=25, second=0, microsecond=0).isoformat(),
                2,
            ),
        )
        db.commit()


def _build_skill(
    *,
    tmp_path: Path,
    router: DummyRouter | None = None,
    heartbeat_service: DummyHeartbeatService | None = None,
    output_root: Path | None = None,
    permission_manager: PermissionManager | None = None,
) -> tuple[InfoReachSkill, DummyQueue, StructuredStore]:
    queue = DummyQueue()
    store = StructuredStore(db_path=tmp_path / "hypo.db")
    skill = InfoReachSkill(
        structured_store=store,
        model_router=router,
        message_queue=queue,
        permission_manager=permission_manager,
        heartbeat_service=heartbeat_service,
        output_root=output_root or (tmp_path / "trendradar-output"),
    )
    return skill, queue, store


def test_trend_query_reads_latest_sqlite_and_filters_platform_keyword(tmp_path: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    output_root = tmp_path / "trendradar-output"
    _write_news_db(output_root / "news" / f"{today}.db")
    _write_rss_db(output_root / "rss" / f"{today}.db")

    permission_manager = PermissionManager(
        DirectoryWhitelist(
            rules=[{"path": str(output_root), "permissions": ["read"]}],
            default_policy="readonly",
            blocked_paths=[],
        )
    )
    skill, _, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        permission_manager=permission_manager,
    )

    result = asyncio.run(
        skill.trend_query(platform="weibo", keyword="阿里云", time_range="today")
    )

    assert result["count"] == 1
    item = result["items"][0]
    assert item["title"] == "阿里云部分产品涨价 34%"
    assert item["platform"] == "weibo"
    assert item["url"] == "https://example.com/weibo-ai"
    assert item["ai_analysis"]["tag"] == "大模型与AI产品"


def test_trend_summary_reads_latest_html_report_when_available(tmp_path: Path) -> None:
    output_root = tmp_path / "trendradar-output"
    report_dir = output_root / "html" / "latest"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "current.html").write_text(
        """
        <div class="ai-block-title">核心热点态势</div>
        <div class="ai-block-content">阿里云涨价与 AI agent 工具成为技术主线。</div>
        <div class="ai-block-title">行业建议</div>
        <div class="ai-block-content">财经侧关注算力成本与云厂商定价。</div>
        """,
        encoding="utf-8",
    )
    skill, _, _ = _build_skill(tmp_path=tmp_path, output_root=output_root)

    result = asyncio.run(skill.trend_summary(time_range="today"))

    assert result["source"] == "report_html"
    assert result["report_path"].endswith("current.html")
    assert "阿里云涨价" in result["categories"]["技术"]
    assert "云厂商定价" in result["categories"]["财经"]


def test_trend_summary_reads_root_index_html_as_report_fallback(tmp_path: Path) -> None:
    output_root = tmp_path / "trendradar-output"
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "index.html").write_text(
        """
        <div class="ai-block-title">今日热点总览</div>
        <div class="ai-block-content">AI agent 与云成本是今天的核心趋势。</div>
        """,
        encoding="utf-8",
    )
    skill, _, _ = _build_skill(tmp_path=tmp_path, output_root=output_root)

    result = asyncio.run(skill.trend_summary(time_range="today"))

    assert result["source"] == "report_html"
    assert result["report_path"].endswith("index.html")
    assert "AI agent" in result["categories"]["技术"]


def test_trend_summary_falls_back_to_lightweight_model_without_report(tmp_path: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    output_root = tmp_path / "trendradar-output"
    _write_news_db(output_root / "news" / f"{today}.db")
    router = DummyRouter(
        payload={
            "技术": "模型给出的技术摘要",
            "学术": "模型给出的学术摘要",
            "娱乐": "模型给出的娱乐摘要",
            "财经": "模型给出的财经摘要",
        }
    )
    skill, _, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        router=router,
    )

    result = asyncio.run(skill.trend_summary(time_range="today"))

    assert result["source"] == "lightweight_model"
    assert result["categories"]["技术"] == "模型给出的技术摘要"
    assert len(router.prompts) == 1


def test_trend_summary_reports_heuristic_source_when_lightweight_model_fails(
    tmp_path: Path,
) -> None:
    today = datetime.now(UTC).date().isoformat()
    output_root = tmp_path / "trendradar-output"
    _write_news_db(output_root / "news" / f"{today}.db")
    skill, _, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        router=FailingRouter(),
    )

    result = asyncio.run(skill.trend_summary(time_range="today"))

    assert result["source"] == "heuristic"
    assert "阿里云部分产品涨价" in result["categories"]["技术"]


def test_trend_subscription_crud_and_heartbeat_registration(tmp_path: Path) -> None:
    output_root = tmp_path / "trendradar-output"
    heartbeat_service = DummyHeartbeatService()
    skill, _, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        heartbeat_service=heartbeat_service,
    )

    created = asyncio.run(
        skill.trend_subscribe(
            name="ai-watch",
            keywords=["AI", "agent"],
            platforms=["weibo", "hackernews"],
            schedule="daily",
        )
    )
    listed = asyncio.run(skill.trend_list_subscriptions())
    deleted = asyncio.run(skill.trend_delete_subscription(name="ai-watch"))

    assert heartbeat_service.registrations[0][0] == "trendradar"
    assert created["name"] == "ai-watch"
    assert listed["items"][0]["keywords"] == ["AI", "agent"]
    assert listed["items"][0]["platforms"] == ["weibo", "hackernews"]
    assert deleted["deleted"] is True


def test_run_scheduled_summary_pushes_summary_and_subscription_updates(tmp_path: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    output_root = tmp_path / "trendradar-output"
    _write_news_db(output_root / "news" / f"{today}.db")
    _write_rss_db(output_root / "rss" / f"{today}.db")
    router = DummyRouter()
    skill, queue, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        router=router,
    )
    asyncio.run(
        skill.trend_subscribe(
            name="ai-watch",
            keywords=["AI", "阿里云"],
            platforms=["weibo", "hackernews"],
            schedule="daily",
        )
    )

    result = asyncio.run(skill.run_scheduled_summary())

    assert result["summary_pushed"] is True
    assert result["subscription_pushes"] == 1
    assert len(queue.events) == 2
    assert queue.events[0]["event_type"] == "trendradar_trigger"
    assert "TrendRadar 摘要" in queue.events[0]["title"]
    assert "ai-watch" in queue.events[1]["title"]


def test_trend_query_rejects_blocked_output_root(tmp_path: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    output_root = tmp_path / "trendradar-output"
    _write_news_db(output_root / "news" / f"{today}.db")
    permission_manager = PermissionManager(
        DirectoryWhitelist(
            rules=[],
            default_policy="readonly",
            blocked_paths=[str(output_root)],
        )
    )
    skill, _, _ = _build_skill(
        tmp_path=tmp_path,
        output_root=output_root,
        permission_manager=permission_manager,
    )

    result = asyncio.run(skill.execute("trend_query", {"time_range": "today"}))

    assert result.status == "error"
    assert "permission denied" in result.error_info.lower()
