"""Hypo-Info-backed information intelligence skill.

已迁移到 Hypo-Info API，TrendRadar 并行运行中。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import aiosqlite
import httpx

from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.exceptions import ExternalServiceError
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

_LOG = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8200"
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)


class HypoInfoClient:
    """Async HTTP client for Hypo-Info REST API."""

    def __init__(self, base_url: str, transport: Any | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"base_url": self._base_url, "timeout": _TIMEOUT}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def query(
        self,
        *,
        category: str | None = None,
        keyword: str | None = None,
        time_range: str = "today",
        min_importance: int | None = None,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"time_range": time_range}
        if category:
            params["category"] = category
        if keyword:
            params["keyword"] = keyword
        if min_importance is not None:
            params["min_importance"] = min_importance
        if source_name:
            params["source_name"] = source_name
        return await self._get("/api/agent/query", params)

    async def digest(self, *, time_range: str = "today") -> dict[str, Any]:
        return await self._get("/api/agent/digest", {"time_range": time_range})

    async def summary(
        self,
        *,
        time_range: str = "today",
        min_importance: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"time_range": time_range}
        if min_importance is not None:
            params["min_importance"] = min_importance
        return await self._get("/api/agent/summary", params)

    async def categories(self) -> dict[str, Any]:
        return await self._get("/api/agent/categories", {})

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException as exc:
            raise HypoInfoError(f"Hypo-Info 服务不可达：{exc}") from exc
        except httpx.RequestError as exc:
            raise HypoInfoError(f"Hypo-Info 服务不可达：{exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise HypoInfoError(
                f"Hypo-Info 返回错误状态 {exc.response.status_code}"
            ) from exc


class HypoInfoError(ExternalServiceError):
    """Raised when Hypo-Info cannot fulfill a request."""


class InfoReachSkill(BaseSkill):
    """Hypo-Info 主动推送与订阅管理。

    由 Scheduler 和 Heartbeat 驱动，负责定时新闻摘要推送、
    高重要性文章主动通知、订阅 CRUD。

    数据通过 HypoInfoClient 调用 /api/agent/* 端点。
    """

    name = "info_reach"
    description = "Use Hypo-Info for proactive digests, article retrieval, and subscription management."
    required_permissions: list[str] = []

    HEARTBEAT_PREF_KEY = "info_reach.last_heartbeat_check_at"

    def __init__(
        self,
        *,
        message_queue: Any | None = None,
        heartbeat_service: Any | None = None,
        db_path: Path | str | None = None,
        base_url: str | None = None,
        secrets_path: Path | str = "config/secrets.yaml",
        transport: Any | None = None,
        default_session_id: str = "main",
        now_fn: Callable[[], datetime] | None = None,
        # legacy / unused — kept for call-site compatibility
        structured_store: Any | None = None,
        model_router: Any | None = None,
        permission_manager: Any | None = None,
        output_root: Any | None = None,
    ) -> None:
        self.message_queue = message_queue
        self.default_session_id = default_session_id
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._db_path = Path(db_path) if db_path else None
        self.secrets_path = Path(secrets_path)
        resolved_base_url = self._resolve_base_url(base_url)
        self._client = HypoInfoClient(resolved_base_url, transport=transport)
        self._subscription_table_ready = False

        if heartbeat_service is not None and hasattr(heartbeat_service, "register_event_source"):
            heartbeat_service.register_event_source("hypo_info", self._check_new_info)

    # ------------------------------------------------------------------
    # Tool manifest
    # ------------------------------------------------------------------

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "info_query",
                    "description": (
                        "Retrieve Hypo-Info articles for internal news lookup. "
                        "After calling, answer the user with a concise natural-language summary of the findings. "
                        "Do not dump raw JSON, field names, or internal payloads."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "keyword": {"type": "string"},
                            "time_range": {
                                "type": "string",
                                "enum": ["today", "yesterday", "3d", "7d"],
                                "default": "today",
                            },
                            "min_importance": {"type": "integer"},
                            "source_name": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_summary",
                    "description": (
                        "Retrieve a Hypo-Info digest for proactive updates. "
                        "Summarize the digest in natural-language sections for the user instead of repeating raw JSON."
                    ),
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
                    "name": "info_subscribe",
                    "description": (
                        "Create or update a Hypo-Info subscription. "
                        "Confirm the saved subscription in natural language; do not echo raw JSON."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "categories": {"type": "array", "items": {"type": "string"}},
                            "schedule": {"type": "string", "default": "daily"},
                        },
                        "required": ["name", "keywords"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_list_subscriptions",
                    "description": (
                        "List Hypo-Info subscriptions. "
                        "Present them as a readable summary instead of raw JSON."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_delete_subscription",
                    "description": (
                        "Delete a Hypo-Info subscription and confirm the result in natural language."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
        ]

    # ------------------------------------------------------------------
    # execute() dispatch
    # ------------------------------------------------------------------

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "info_query":
                result = await self.info_query(
                    category=str(params.get("category") or "").strip() or None,
                    keyword=str(params.get("keyword") or "").strip() or None,
                    time_range=str(params.get("time_range") or "today").strip() or "today",
                    min_importance=params.get("min_importance"),
                    source_name=str(params.get("source_name") or "").strip() or None,
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "info_summary":
                result = await self.info_summary(
                    time_range=str(params.get("time_range") or "today").strip() or "today",
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "info_subscribe":
                name = str(params.get("name") or "").strip()
                keywords = self._normalize_string_list(params.get("keywords"))
                categories = self._normalize_string_list(params.get("categories"))
                schedule = str(params.get("schedule") or "daily").strip() or "daily"
                if not name:
                    return SkillOutput(status="error", error_info="name is required")
                if not keywords:
                    return SkillOutput(status="error", error_info="keywords is required")
                result = await self.info_subscribe(
                    name=name, keywords=keywords, categories=categories, schedule=schedule
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "info_list_subscriptions":
                return SkillOutput(status="success", result=await self.info_list_subscriptions())
            if tool_name == "info_delete_subscription":
                name = str(params.get("name") or "").strip()
                if not name:
                    return SkillOutput(status="error", error_info="name is required")
                return SkillOutput(status="success", result=await self.info_delete_subscription(name=name))
        except HypoInfoError as exc:
            return SkillOutput(status="error", error_info=f"Hypo-Info 错误：{exc}")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return SkillOutput(status="error", error_info=str(exc))

        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def info_query(
        self,
        *,
        category: str | None = None,
        keyword: str | None = None,
        time_range: str = "today",
        min_importance: int | None = None,
        source_name: str | None = None,
    ) -> str:
        data = await self._client.query(
            category=category,
            keyword=keyword,
            time_range=time_range,
            min_importance=min_importance,
            source_name=source_name,
        )
        articles = data.get("articles") or []
        if not articles:
            return "暂无相关文章。"
        lines: list[str] = []
        for art in articles:
            lines.append(f"【{art.get('title', '')}】")
            if art.get("summary"):
                lines.append(f"  摘要：{art['summary']}")
            lines.append(f"  重要性：{art.get('importance', '')}")
            lines.append(f"  来源：{art.get('source_name', '')}")
            if art.get("url"):
                lines.append(f"  链接：{art['url']}")
        return "\n".join(lines)

    async def info_summary(self, *, time_range: str = "today") -> str:
        data = await self._client.digest(time_range=time_range)
        lines: list[str] = []
        if data.get("highlight"):
            lines.append(data["highlight"])
        for section in data.get("sections") or []:
            category = section.get("category", "")
            items = section.get("items") or []
            if category:
                lines.append(f"\n【{category}】")
            for item in items:
                lines.append(f"  - {item}")
        stats = data.get("stats") or {}
        if stats.get("total_articles"):
            lines.append(f"\n共 {stats['total_articles']} 篇文章")
        return "\n".join(lines).strip()

    async def info_subscribe(
        self,
        *,
        name: str,
        keywords: list[str],
        categories: list[str] | None = None,
        schedule: str = "daily",
    ) -> dict[str, Any]:
        await self._ensure_subscription_table()
        now_iso = self.now_fn().astimezone(UTC).isoformat()
        payload: dict[str, Any] = {
            "name": name,
            "keywords": keywords,
            "categories": categories or [],
            "schedule": schedule,
            "last_run": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        async with aiosqlite.connect(self._store_db_path()) as db:
            await db.execute(
                """
                INSERT INTO info_subscriptions(
                    name, keywords_json, categories_json, schedule, last_run, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    keywords_json=excluded.keywords_json,
                    categories_json=excluded.categories_json,
                    schedule=excluded.schedule,
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(categories or [], ensure_ascii=False),
                    schedule,
                    None,
                    now_iso,
                    now_iso,
                ),
            )
            await db.commit()
        return payload

    async def info_list_subscriptions(self) -> dict[str, Any]:
        await self._ensure_subscription_table()
        async with aiosqlite.connect(self._store_db_path()) as db:
            async with db.execute(
                """
                SELECT name, keywords_json, categories_json, schedule, last_run, created_at, updated_at
                FROM info_subscriptions
                ORDER BY created_at ASC, name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return {"items": [self._subscription_row_to_dict(row) for row in rows]}

    async def info_delete_subscription(self, *, name: str) -> dict[str, Any]:
        await self._ensure_subscription_table()
        async with aiosqlite.connect(self._store_db_path()) as db:
            cursor = await db.execute(
                "DELETE FROM info_subscriptions WHERE name = ?", (name,)
            )
            await db.commit()
            deleted = int(getattr(cursor, "rowcount", 0) or 0) > 0
        return {"name": name, "deleted": deleted}

    async def run_scheduled_summary(self) -> dict[str, Any]:
        """Called by scheduler for hypo_info_digest jobs."""
        text = await self.info_summary(time_range="today")
        pushed = False
        if text and self.message_queue is not None:
            await self.message_queue.put(
                {
                    "event_type": "hypo_info_trigger",
                    "session_id": self.default_session_id,
                    "title": "Hypo-Info 摘要",
                    "summary": text,
                }
            )
            pushed = True
        sub_pushes = await self._run_due_subscriptions()
        return {"summary_pushed": pushed, "subscription_pushes": sub_pushes}

    async def _check_new_info(self) -> dict[str, Any]:
        subscriptions = (await self.info_list_subscriptions())["items"]
        if not subscriptions:
            return {"name": "hypo_info", "new_items": 0, "items": []}

        data = await self._client.query(time_range="today", min_importance=7)
        articles = data.get("articles") or []
        matches: list[dict[str, Any]] = []
        for sub in subscriptions:
            for art in articles:
                if self._article_matches_subscription(art, sub):
                    matches.append(
                        {
                            "subscription": sub["name"],
                            "title": art.get("title", ""),
                            "url": art.get("url", ""),
                            "category": art.get("category_l1", ""),
                        }
                    )
        return {"name": "hypo_info", "new_items": len(matches), "items": matches[:6]}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_db_path(self) -> Path:
        if self._db_path is not None:
            return self._db_path
        raise RuntimeError("db_path not configured")

    async def _ensure_subscription_table(self) -> None:
        if self._subscription_table_ready:
            return
        db_path = self._store_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS info_subscriptions (
                    name TEXT PRIMARY KEY,
                    keywords_json TEXT NOT NULL,
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    schedule TEXT NOT NULL DEFAULT 'daily',
                    last_run TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()
            await self._maybe_migrate_from_trendradar(db)
        self._subscription_table_ready = True

    async def _maybe_migrate_from_trendradar(self, db: aiosqlite.Connection) -> None:
        """One-time idempotent migration from trendradar_subscriptions."""
        # Check if old table exists
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trendradar_subscriptions'"
        ) as cur:
            old_exists = await cur.fetchone()
        if not old_exists:
            return
        # Only migrate if info_subscriptions is empty
        async with db.execute("SELECT COUNT(*) FROM info_subscriptions") as cur:
            row = await cur.fetchone()
            if row and int(row[0]) > 0:
                return
        # Migrate
        async with db.execute(
            "SELECT name, keywords_json, platforms_json, schedule, last_run, created_at, updated_at "
            "FROM trendradar_subscriptions"
        ) as cur:
            old_rows = await cur.fetchall()
        count = 0
        for row in old_rows:
            name, keywords_json, platforms_json, schedule, last_run, created_at, updated_at = row
            await db.execute(
                """
                INSERT OR IGNORE INTO info_subscriptions
                (name, keywords_json, categories_json, schedule, last_run, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, keywords_json, platforms_json, schedule, last_run, created_at, updated_at),
            )
            count += 1
        await db.commit()
        _LOG.info("Migrated %d subscriptions from trendradar_subscriptions to info_subscriptions", count)

    def _subscription_row_to_dict(self, row: tuple) -> dict[str, Any]:
        name, keywords_json, categories_json, schedule, last_run, created_at, updated_at = row
        return {
            "name": name,
            "keywords": json.loads(keywords_json or "[]"),
            "categories": json.loads(categories_json or "[]"),
            "schedule": schedule,
            "last_run": last_run,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _article_matches_subscription(self, article: dict[str, Any], sub: dict[str, Any]) -> bool:
        title = str(article.get("title") or "").lower()
        summary = str(article.get("summary") or "").lower()
        cat_l1 = str(article.get("category_l1") or "").lower()
        keywords = [k.lower() for k in (sub.get("keywords") or [])]
        categories = [c.lower() for c in (sub.get("categories") or [])]
        keyword_match = any(kw in title or kw in summary for kw in keywords)
        category_match = not categories or any(c in cat_l1 for c in categories)
        return keyword_match and category_match

    async def _run_due_subscriptions(self) -> int:
        subscriptions = (await self.info_list_subscriptions())["items"]
        pushed = 0
        for sub in subscriptions:
            data = await self._client.query(time_range="today", min_importance=5)
            articles = data.get("articles") or []
            matched = [a for a in articles if self._article_matches_subscription(a, sub)]
            if not matched or self.message_queue is None:
                continue
            summary_text = "；".join(str(a.get("title", "")) for a in matched[:3])
            await self.message_queue.put(
                {
                    "event_type": "hypo_info_trigger",
                    "session_id": self.default_session_id,
                    "title": f"Hypo-Info 订阅：{sub['name']}",
                    "summary": summary_text,
                }
            )
            pushed += 1
        return pushed

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _resolve_base_url(self, explicit_base_url: str | None) -> str:
        if explicit_base_url and str(explicit_base_url).strip():
            return str(explicit_base_url).strip()
        try:
            secrets = load_secrets_config(self.secrets_path)
        except (FileNotFoundError, ValueError):
            return _DEFAULT_BASE_URL
        services = secrets.services
        hypo_info = services.hypo_info if services is not None else None
        configured = str(hypo_info.base_url if hypo_info is not None else "").strip()
        return configured or _DEFAULT_BASE_URL
