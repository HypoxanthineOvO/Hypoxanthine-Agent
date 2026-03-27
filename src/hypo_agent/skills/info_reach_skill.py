"""TrendRadar-backed trend intelligence skill.

Future HTTP API design notes (not implemented in this milestone):
- `GET /api/skills/info-reach/trends?platform=weibo&keyword=ai&time_range=today`
- `GET /api/skills/info-reach/summary?time_range=today`
- `POST /api/skills/info-reach/subscriptions`
- `GET /api/skills/info-reach/subscriptions`
- `DELETE /api/skills/info-reach/subscriptions/{name}`
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
import html
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable

import aiosqlite

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

_AI_BLOCK_RE = re.compile(
    r'<div class="ai-block-title">(.*?)</div>\s*<div class="ai-block-content">(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_HOTLIST_DATE_RE = re.compile(r"^(?P<hour>\d{1,2})[-:](?P<minute>\d{2})$")


class InfoReachSkill(BaseSkill):
    name = "info_reach"
    description = "Read TrendRadar trend data, summaries, and subscriptions from local output."
    required_permissions: list[str] = []

    HEARTBEAT_PREF_KEY = "info_reach.last_heartbeat_check_at"

    def __init__(
        self,
        *,
        structured_store: Any,
        permission_manager: Any | None = None,
        model_router: Any | None = None,
        message_queue: Any | None = None,
        heartbeat_service: Any | None = None,
        output_root: Path | str = "~/trendradar/output",
        default_session_id: str = "main",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.structured_store = structured_store
        self.permission_manager = permission_manager
        self.model_router = model_router
        self.message_queue = message_queue
        self.default_session_id = default_session_id
        self.output_root = Path(output_root).expanduser().resolve(strict=False)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._subscription_table_ready = False
        self._last_heartbeat_check_at: datetime | None = None

        if heartbeat_service is not None and hasattr(heartbeat_service, "register_event_source"):
            heartbeat_service.register_event_source("trendradar", self._check_new_trends)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "trend_query",
                    "description": "查询 TrendRadar 热榜与 RSS 数据，支持平台和关键词过滤。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "keyword": {"type": "string"},
                            "time_range": {
                                "type": "string",
                                "enum": ["today", "yesterday", "3d", "7d"],
                                "default": "today",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "trend_summary",
                    "description": "读取 TrendRadar 最新 AI 分析报告并输出分类摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "time_range": {
                                "type": "string",
                                "enum": ["today", "yesterday", "3d", "7d"],
                                "default": "today",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "trend_subscribe",
                    "description": "创建或更新一个 TrendRadar 订阅。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "platforms": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "schedule": {"type": "string", "default": "daily"},
                        },
                        "required": ["name", "keywords"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "trend_list_subscriptions",
                    "description": "列出全部 TrendRadar 订阅。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "trend_delete_subscription",
                    "description": "删除一个 TrendRadar 订阅。",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "trend_query":
                result = await self.trend_query(
                    platform=str(params.get("platform") or "").strip() or None,
                    keyword=str(params.get("keyword") or "").strip() or None,
                    time_range=str(params.get("time_range") or "today").strip() or "today",
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "trend_summary":
                result = await self.trend_summary(
                    time_range=str(params.get("time_range") or "today").strip() or "today",
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "trend_subscribe":
                name = str(params.get("name") or "").strip()
                keywords = self._normalize_string_list(params.get("keywords"))
                platforms = self._normalize_string_list(params.get("platforms"))
                schedule = str(params.get("schedule") or "daily").strip() or "daily"
                if not name:
                    return SkillOutput(status="error", error_info="name is required")
                if not keywords:
                    return SkillOutput(status="error", error_info="keywords is required")
                result = await self.trend_subscribe(
                    name=name,
                    keywords=keywords,
                    platforms=platforms or None,
                    schedule=schedule,
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "trend_list_subscriptions":
                return SkillOutput(
                    status="success",
                    result=await self.trend_list_subscriptions(),
                )
            if tool_name == "trend_delete_subscription":
                name = str(params.get("name") or "").strip()
                if not name:
                    return SkillOutput(status="error", error_info="name is required")
                deleted = await self.trend_delete_subscription(name=name)
                return SkillOutput(status="success", result=deleted)
        except PermissionError as exc:
            return SkillOutput(status="error", error_info=f"Permission denied: {exc}")
        except Exception as exc:
            return SkillOutput(status="error", error_info=str(exc))

        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def trend_query(
        self,
        *,
        platform: str | None = None,
        keyword: str | None = None,
        time_range: str = "today",
        since: datetime | None = None,
    ) -> dict[str, Any]:
        self._ensure_output_readable(self.output_root)
        start_at, end_at = self._resolve_time_window(time_range=time_range, since=since)
        items = self._load_trend_items(
            start_at=start_at,
            end_at=end_at,
            platform=platform,
            keyword=keyword,
        )
        return {
            "platform": platform,
            "keyword": keyword,
            "time_range": time_range,
            "count": len(items),
            "items": items,
        }

    async def trend_summary(self, *, time_range: str = "today") -> dict[str, Any]:
        query_result = await self.trend_query(time_range=time_range)
        report = self._load_latest_report()
        category_lists, summary_source = await self._build_summary_categories(
            time_range=time_range,
            report=report,
            items=query_result["items"],
        )
        return {
            "time_range": time_range,
            "source": "report_html" if report is not None else summary_source,
            "report": report,
            "report_path": str(report["path"]) if report is not None else None,
            "categories": {
                key: "；".join(values) if values else "暂无显著更新"
                for key, values in category_lists.items()
            },
        }

    async def trend_subscribe(
        self,
        *,
        name: str,
        keywords: list[str],
        platforms: list[str] | None = None,
        schedule: str = "daily",
    ) -> dict[str, Any]:
        await self._ensure_subscription_table()
        now_iso = self.now_fn().astimezone(UTC).isoformat()
        payload = {
            "name": name,
            "keywords": keywords,
            "platforms": platforms or [],
            "schedule": schedule,
            "last_run": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        async with aiosqlite.connect(self._store_db_path()) as db:
            await db.execute(
                """
                INSERT INTO trendradar_subscriptions(
                    name, keywords_json, platforms_json, schedule, last_run, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    keywords_json=excluded.keywords_json,
                    platforms_json=excluded.platforms_json,
                    schedule=excluded.schedule,
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(platforms or [], ensure_ascii=False),
                    schedule,
                    None,
                    now_iso,
                    now_iso,
                ),
            )
            await db.commit()
        return payload

    async def trend_list_subscriptions(self) -> dict[str, Any]:
        await self._ensure_subscription_table()
        async with aiosqlite.connect(self._store_db_path()) as db:
            async with db.execute(
                """
                SELECT name, keywords_json, platforms_json, schedule, last_run, created_at, updated_at
                FROM trendradar_subscriptions
                ORDER BY created_at ASC, name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return {"items": [self._subscription_row_to_dict(row) for row in rows]}

    async def trend_delete_subscription(self, *, name: str) -> dict[str, Any]:
        await self._ensure_subscription_table()
        async with aiosqlite.connect(self._store_db_path()) as db:
            cursor = await db.execute(
                "DELETE FROM trendradar_subscriptions WHERE name = ?",
                (name,),
            )
            await db.commit()
            deleted = int(getattr(cursor, "rowcount", 0) or 0) > 0
        return {"name": name, "deleted": deleted}

    async def run_scheduled_summary(self) -> dict[str, Any]:
        summary_result = await self.trend_summary(time_range="today")
        pushed = 0
        summary_text = self._format_category_summary(summary_result["categories"])
        summary_pushed = False
        if summary_text:
            summary_pushed = await self._push_queue_message(
                title="TrendRadar 摘要",
                summary=summary_text,
                metadata={"source": "trendradar_summary"},
            )
            pushed += int(summary_pushed)
        subscription_pushes = await self._run_due_subscriptions()
        pushed += subscription_pushes
        return {
            "pushed": pushed,
            "summary_pushed": summary_pushed,
            "subscription_pushes": subscription_pushes,
            "categories": summary_result["categories"],
        }

    async def _check_new_trends(self) -> dict[str, Any]:
        subscriptions = (await self.trend_list_subscriptions())["items"]
        if not subscriptions:
            return {"name": "trendradar", "new_items": 0, "items": []}

        since = await self._load_last_heartbeat_check_at()
        query = await self.trend_query(time_range="today", since=since)
        items = query["items"]
        matches: list[dict[str, Any]] = []
        for subscription in subscriptions:
            subscription_matches = self._filter_items_for_subscription(items, subscription)
            for item in subscription_matches[:3]:
                matches.append(
                    {
                        "subscription": subscription["name"],
                        "title": item["title"],
                        "url": item["url"],
                        "platform": item["platform"],
                    }
                )
        await self._persist_last_heartbeat_check_at(self.now_fn().astimezone(UTC))
        return {
            "name": "trendradar",
            "new_items": len(matches),
            "items": matches[:6],
        }

    async def _run_due_subscriptions(self) -> int:
        now = self.now_fn().astimezone(UTC)
        subscriptions = (await self.trend_list_subscriptions())["items"]
        pushed = 0
        for subscription in subscriptions:
            if not self._is_subscription_due(subscription, now):
                continue
            query_result = await self.trend_query(time_range="today")
            matched_items = self._filter_items_for_subscription(query_result["items"], subscription)
            summary_text = await self._summarize_subscription_items(subscription, matched_items)
            if summary_text:
                pushed += int(
                    await self._push_queue_message(
                        title=f"TrendRadar 订阅：{subscription['name']}",
                        summary=summary_text,
                        metadata={"subscription": subscription["name"]},
                    )
                )
            await self._set_subscription_last_run(subscription["name"], now)
        return pushed

    async def _build_summary_categories(
        self,
        *,
        time_range: str,
        report: dict[str, Any] | None,
        items: list[dict[str, Any]],
    ) -> tuple[dict[str, list[str]], str]:
        prompt = self._build_summary_prompt(time_range=time_range, report=report, items=items)
        if self.model_router is not None and hasattr(self.model_router, "call_lightweight_json"):
            try:
                payload = await self.model_router.call_lightweight_json(
                    prompt,
                    session_id=self.default_session_id,
                )
            except TypeError:
                payload = await self.model_router.call_lightweight_json(prompt)
            except Exception:
                payload = {}
            normalized = self._normalize_summary_categories(payload)
            if any(normalized.values()):
                return normalized, "lightweight_model"
        return self._heuristic_summary_categories(items, report), "heuristic"

    def _build_summary_prompt(
        self,
        *,
        time_range: str,
        report: dict[str, Any] | None,
        items: list[dict[str, Any]],
    ) -> str:
        prompt = (
            "你是 TrendRadar 摘要助手。请根据下面的热点报告和条目，只输出 JSON 对象。"
            '键固定为 "技术" "学术" "娱乐" "财经"，每个值是 0-3 条中文摘要数组。'
        )
        prompt += f"\n时间范围: {time_range}"
        if report is not None:
            prompt += "\n\n[AI 报告]"
            for section in report.get("sections", []):
                prompt += f"\n- {section.get('title')}: {section.get('content')}"
        prompt += "\n\n[热点条目]"
        for item in items[:30]:
            prompt += (
                f"\n- 平台={item.get('platform')} 标题={item.get('title')} "
                f"摘要={item.get('summary') or ''} 标签={self._item_ai_tag(item)}"
            )
        return prompt

    def _normalize_summary_categories(self, payload: Any) -> dict[str, list[str]]:
        keys = ("技术", "学术", "娱乐", "财经")
        if not isinstance(payload, dict):
            return {}
        result: dict[str, list[str]] = {}
        for key in keys:
            values = payload.get(key)
            if isinstance(values, list):
                result[key] = [str(item).strip() for item in values if str(item).strip()][:3]
            elif isinstance(values, str) and values.strip():
                result[key] = [values.strip()]
            else:
                result[key] = []
        return result

    def _heuristic_summary_categories(
        self,
        items: list[dict[str, Any]],
        report: dict[str, Any] | None,
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {"技术": [], "学术": [], "娱乐": [], "财经": []}
        if report is not None:
            for section in report.get("sections", []):
                content = str(section.get("content") or "").strip()
                if not content:
                    continue
                category = self._guess_item_category(
                    {
                        "title": str(section.get("title") or ""),
                        "summary": content,
                        "platform": "report",
                    }
                )
                grouped[category].append(content)
        for item in items:
            category = self._guess_item_category(item)
            if not grouped[category]:
                grouped[category].append(str(item.get("title") or ""))
        return grouped

    async def _summarize_subscription_items(
        self,
        subscription: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> str:
        if not items:
            return ""
        if self.model_router is not None and hasattr(self.model_router, "call_lightweight_json"):
            prompt = (
                "根据下面的 TrendRadar 订阅命中结果，输出 JSON，包含 title 和 summary 两个字段。"
                f"\n订阅名: {subscription['name']}"
            )
            for item in items[:10]:
                prompt += (
                    f"\n- 平台={item['platform']} 标题={item['title']} "
                    f"摘要={item.get('summary') or ''}"
                )
            try:
                payload = await self.model_router.call_lightweight_json(
                    prompt,
                    session_id=self.default_session_id,
                )
            except TypeError:
                payload = await self.model_router.call_lightweight_json(prompt)
            except Exception:
                payload = {}
            summary = str(payload.get("summary") or "").strip()
            if summary:
                return summary
        return "；".join(str(item["title"]) for item in items[:3])

    def _format_category_summary(self, categories: dict[str, list[str]]) -> str:
        lines: list[str] = []
        for category in ("技术", "学术", "娱乐", "财经"):
            raw = categories.get(category, [])
            if isinstance(raw, str):
                entries = [raw.strip()] if raw.strip() else []
            else:
                entries = [str(item).strip() for item in raw if str(item).strip()]
            if not entries:
                continue
            lines.append(f"{category}:")
            for entry in entries:
                lines.append(f"- {entry}")
        return "\n".join(lines).strip()

    def _load_trend_items(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        platform: str | None,
        keyword: str | None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for db_path in self._candidate_db_paths("news", start_at, end_at):
            self._ensure_output_readable(db_path)
            items.extend(self._load_news_items(db_path, start_at=start_at, end_at=end_at))
        for db_path in self._candidate_db_paths("rss", start_at, end_at):
            self._ensure_output_readable(db_path)
            items.extend(self._load_rss_items(db_path, start_at=start_at, end_at=end_at))

        filtered = [
            item
            for item in items
            if self._matches_platform(item, platform) and self._matches_keyword(item, keyword)
        ]
        filtered.sort(
            key=lambda item: (
                str(item.get("sort_at") or ""),
                -(int(item.get("heat", {}).get("crawl_count") or 0)),
                -(float(item.get("heat", {}).get("rank") or 9999)),
            ),
            reverse=True,
        )
        for item in filtered:
            item.pop("sort_at", None)
        return filtered[:50]

    def _candidate_db_paths(self, group: str, start_at: datetime, end_at: datetime) -> list[Path]:
        group_dir = self.output_root / group
        if not group_dir.exists():
            return []
        paths: list[Path] = []
        current = start_at.date()
        while current <= end_at.date():
            path = group_dir / f"{current.isoformat()}.db"
            if path.exists():
                paths.append(path)
            current += timedelta(days=1)
        return sorted(paths)

    def _load_news_items(
        self,
        db_path: Path,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        ai_map = self._load_hotlist_ai_map(db_path)
        db_date = self._date_from_db_path(db_path)
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT n.id, n.title, n.platform_id, n.rank, n.url, n.last_crawl_time, n.crawl_count,
                       COALESCE(p.name, n.platform_id) AS platform_name
                FROM news_items n
                LEFT JOIN platforms p ON p.id = n.platform_id
                """
            ).fetchall()
        finally:
            conn.close()
        items: list[dict[str, Any]] = []
        for row in rows:
            item_dt = self._parse_hotlist_datetime(db_date, str(row[5] or ""))
            if item_dt < start_at or item_dt > end_at:
                continue
            ai_payload = ai_map.get(int(row[0]))
            items.append(
                {
                    "title": str(row[1] or ""),
                    "url": str(row[4] or ""),
                    "platform": str(row[2] or ""),
                    "platform_display": str(row[7] or row[2] or ""),
                    "summary": "",
                    "published_at": item_dt.isoformat(),
                    "source_type": "hotlist",
                    "heat": {
                        "rank": int(row[3] or 0),
                        "crawl_count": int(row[6] or 0),
                    },
                    "ai_analysis": ai_payload,
                    "sort_at": item_dt.isoformat(),
                }
            )
        return items

    def _load_hotlist_ai_map(self, db_path: Path) -> dict[int, dict[str, Any]]:
        conn = sqlite3.connect(db_path)
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT r.news_item_id, t.tag, r.relevance_score
                    FROM ai_filter_results r
                    LEFT JOIN ai_filter_tags t ON t.id = r.tag_id
                    WHERE r.source_type = 'hotlist' AND COALESCE(r.status, 'active') = 'active'
                    ORDER BY r.relevance_score DESC, r.id DESC
                    """
                ).fetchall()
            except sqlite3.Error:
                rows = conn.execute(
                    """
                    SELECT r.news_item_id, t.tag, r.relevance_score
                    FROM ai_filter_results r
                    LEFT JOIN ai_filter_tags t ON t.id = r.tag_id
                    WHERE r.source_type = 'hotlist'
                    ORDER BY r.relevance_score DESC, r.id DESC
                    """
                ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

        result: dict[int, dict[str, Any]] = {}
        for news_item_id, tag, relevance_score in rows:
            key = int(news_item_id or 0)
            if key <= 0 or key in result:
                continue
            result[key] = {
                "tag": str(tag or ""),
                "relevance_score": float(relevance_score or 0),
            }
        return result

    def _load_rss_items(
        self,
        db_path: Path,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(db_path)
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT i.title, i.feed_id, i.url, i.published_at, i.summary, COALESCE(f.name, i.feed_id)
                    FROM rss_items i
                    LEFT JOIN rss_feeds f ON f.id = i.feed_id
                    """
                ).fetchall()
            except sqlite3.Error:
                rows = conn.execute(
                    """
                    SELECT i.title, i.feed_id, i.url, i.published_at, i.summary, i.feed_id
                    FROM rss_items i
                    """
                ).fetchall()
        finally:
            conn.close()
        items: list[dict[str, Any]] = []
        for row in rows:
            item_dt = self._parse_datetime(str(row[3] or "")) or self._date_from_db_path(db_path).replace(
                tzinfo=UTC
            )
            if item_dt < start_at or item_dt > end_at:
                continue
            items.append(
                {
                    "title": str(row[0] or ""),
                    "url": str(row[2] or ""),
                    "platform": str(row[1] or ""),
                    "platform_display": str(row[5] or row[1] or ""),
                    "summary": str(row[4] or ""),
                    "published_at": item_dt.isoformat(),
                    "source_type": "rss",
                    "heat": {"rank": 0, "crawl_count": 1},
                    "ai_analysis": None,
                    "sort_at": item_dt.isoformat(),
                }
            )
        return items

    def _load_latest_report(self) -> dict[str, Any] | None:
        report_candidates = [
            self.output_root / "index.html",
            self.output_root / "html" / "latest" / "current.html",
            *sorted((self.output_root / "html").glob("*/*.html"), reverse=True),
        ]
        for path in report_candidates:
            if not path.exists() or not path.is_file():
                continue
            self._ensure_output_readable(path)
            content = path.read_text(encoding="utf-8", errors="replace")
            sections = self._extract_ai_sections(content)
            if sections:
                return {
                    "path": str(path),
                    "sections": sections,
                }
        return None

    def _extract_ai_sections(self, html_text: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for raw_title, raw_content in _AI_BLOCK_RE.findall(html_text):
            title = self._clean_html_text(raw_title)
            content = self._clean_html_text(raw_content)
            if title and content:
                sections.append({"title": title, "content": content})
        return sections

    def _clean_html_text(self, value: str) -> str:
        normalized = _BREAK_RE.sub("\n", value)
        normalized = _TAG_RE.sub("", normalized)
        normalized = html.unescape(normalized)
        return "\n".join(line.strip() for line in normalized.splitlines() if line.strip()).strip()

    def _matches_platform(self, item: dict[str, Any], platform: str | None) -> bool:
        if not platform:
            return True
        normalized_target = self._normalize_platform_token(platform)
        aliases = {
            self._normalize_platform_token(str(item.get("platform") or "")),
            self._normalize_platform_token(str(item.get("platform_display") or "")),
        }
        platform_value = str(item.get("platform") or "")
        if platform_value.endswith("-hot-search"):
            aliases.add(self._normalize_platform_token(platform_value.removesuffix("-hot-search")))
        if platform_value.endswith("-hot"):
            aliases.add(self._normalize_platform_token(platform_value.removesuffix("-hot")))
        return normalized_target in aliases

    def _matches_keyword(self, item: dict[str, Any], keyword: str | None) -> bool:
        if not keyword:
            return True
        needle = keyword.lower().strip()
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                self._item_ai_tag(item),
            ]
        ).lower()
        return needle in haystack

    def _filter_items_for_subscription(
        self,
        items: list[dict[str, Any]],
        subscription: dict[str, Any],
    ) -> list[dict[str, Any]]:
        keywords = [str(item).strip() for item in subscription.get("keywords", []) if str(item).strip()]
        platforms = [str(item).strip() for item in subscription.get("platforms", []) if str(item).strip()]
        if not keywords:
            return []
        matches: list[dict[str, Any]] = []
        for item in items:
            if platforms and not any(self._matches_platform(item, platform) for platform in platforms):
                continue
            if any(self._matches_keyword(item, keyword) for keyword in keywords):
                matches.append(item)
        return matches

    def _item_ai_tag(self, item: dict[str, Any]) -> str:
        ai_analysis = item.get("ai_analysis")
        if isinstance(ai_analysis, dict):
            return str(ai_analysis.get("tag") or "")
        return ""

    def _guess_item_category(self, item: dict[str, Any]) -> str:
        text = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                self._item_ai_tag(item),
                str(item.get("platform") or ""),
            ]
        ).lower()
        if any(token in text for token in ("ai", "芯片", "cloud", "agent", "科技", "开源", "hacker")):
            return "技术"
        if any(token in text for token in ("大学", "论文", "研究", "学术", "school")):
            return "学术"
        if any(token in text for token in ("综艺", "演员", "番", "电影", "娱乐", "bilibili")):
            return "娱乐"
        return "财经"

    def _normalize_platform_token(self, value: str) -> str:
        return _NON_ALNUM_RE.sub("", value.lower())

    def _resolve_time_window(
        self,
        *,
        time_range: str,
        since: datetime | None,
    ) -> tuple[datetime, datetime]:
        now = self.now_fn().astimezone(UTC)
        if since is not None:
            return since.astimezone(UTC), now

        normalized = str(time_range or "today").strip().lower()
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        if normalized == "today":
            return day_start, datetime.combine(now.date(), time.max, tzinfo=UTC)
        if normalized == "yesterday":
            yesterday = now.date() - timedelta(days=1)
            return (
                datetime.combine(yesterday, time.min, tzinfo=UTC),
                datetime.combine(yesterday, time.max, tzinfo=UTC),
            )
        if normalized == "3d":
            return day_start - timedelta(days=2), now
        if normalized == "7d":
            return day_start - timedelta(days=6), now
        raise ValueError("Unsupported time_range. Use today/yesterday/3d/7d.")

    def _date_from_db_path(self, path: Path) -> datetime:
        try:
            return datetime.fromisoformat(path.stem)
        except ValueError:
            return self.now_fn().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    def _parse_hotlist_datetime(self, db_date: datetime, raw_value: str) -> datetime:
        matched = _HOTLIST_DATE_RE.match(str(raw_value or "").strip())
        if matched is None:
            return db_date.replace(hour=23, minute=59, second=59, tzinfo=UTC)
        return datetime(
            db_date.year,
            db_date.month,
            db_date.day,
            int(matched.group("hour")),
            int(matched.group("minute")),
            tzinfo=UTC,
        )

    def _parse_datetime(self, raw_value: str) -> datetime | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    async def _ensure_subscription_table(self) -> None:
        if self._subscription_table_ready:
            return
        init_method = getattr(self.structured_store, "init", None)
        if callable(init_method):
            await init_method()
        async with aiosqlite.connect(self._store_db_path()) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trendradar_subscriptions (
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
            await db.commit()
        self._subscription_table_ready = True

    def _store_db_path(self) -> Path:
        raw = getattr(self.structured_store, "db_path", None)
        if raw is None:
            raise ValueError("structured_store must expose db_path for TrendRadar subscriptions")
        return Path(raw)

    def _subscription_row_to_dict(self, row: Any) -> dict[str, Any]:
        keywords = json.loads(str(row[1] or "[]"))
        platforms = json.loads(str(row[2] or "[]"))
        return {
            "name": str(row[0] or ""),
            "keywords": keywords if isinstance(keywords, list) else [],
            "platforms": platforms if isinstance(platforms, list) else [],
            "schedule": str(row[3] or "daily"),
            "last_run": str(row[4] or "") or None,
            "created_at": str(row[5] or ""),
            "updated_at": str(row[6] or ""),
        }

    async def _set_subscription_last_run(self, name: str, run_at: datetime) -> None:
        await self._ensure_subscription_table()
        async with aiosqlite.connect(self._store_db_path()) as db:
            await db.execute(
                """
                UPDATE trendradar_subscriptions
                SET last_run = ?, updated_at = ?
                WHERE name = ?
                """,
                (run_at.isoformat(), run_at.isoformat(), name),
            )
            await db.commit()

    def _is_subscription_due(self, subscription: dict[str, Any], now: datetime) -> bool:
        schedule = str(subscription.get("schedule") or "daily").strip().lower()
        last_run = self._parse_datetime(str(subscription.get("last_run") or ""))
        if last_run is None:
            return True
        delta = now - last_run
        if schedule in {"always", "on_change"}:
            return True
        if schedule in {"hourly", "1h"}:
            return delta >= timedelta(hours=1)
        if schedule in {"8h", "every8h"}:
            return delta >= timedelta(hours=8)
        return delta >= timedelta(days=1)

    async def _push_queue_message(
        self,
        *,
        title: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if self.message_queue is None:
            return False
        await self.message_queue.put(
            {
                "event_type": "trendradar_trigger",
                "session_id": self.default_session_id,
                "title": title,
                "summary": summary,
                "message_tag": "tool_status",
                "metadata": metadata or {},
            }
        )
        return True

    async def _load_last_heartbeat_check_at(self) -> datetime:
        if self._last_heartbeat_check_at is not None:
            return self._last_heartbeat_check_at
        getter = getattr(self.structured_store, "get_preference", None)
        if not callable(getter):
            fallback = self.now_fn().astimezone(UTC) - timedelta(days=1)
            self._last_heartbeat_check_at = fallback
            return fallback
        raw_value = await getter(self.HEARTBEAT_PREF_KEY)
        parsed = self._parse_datetime(str(raw_value or ""))
        if parsed is None:
            parsed = self.now_fn().astimezone(UTC) - timedelta(days=1)
        self._last_heartbeat_check_at = parsed
        return parsed

    async def _persist_last_heartbeat_check_at(self, checked_at: datetime) -> None:
        self._last_heartbeat_check_at = checked_at
        setter = getattr(self.structured_store, "set_preference", None)
        if callable(setter):
            await setter(self.HEARTBEAT_PREF_KEY, checked_at.isoformat())

    def _ensure_output_readable(self, path: Path) -> None:
        if self.permission_manager is None:
            return
        allowed, reason = self.permission_manager.check_permission(str(path), "read", log_allowed=False)
        if not allowed:
            raise PermissionError(reason)

    def _normalize_string_list(self, payload: Any) -> list[str]:
        if not isinstance(payload, list):
            return []
        return [str(item).strip() for item in payload if str(item).strip()]
