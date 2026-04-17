from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import uuid
from typing import Any

import aiosqlite
import structlog

from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.subscription.base import BaseFetcher, FetchResult, NormalizedItem
from hypo_agent.skills.subscription.bilibili_dynamic import BilibiliDynamicFetcher
from hypo_agent.skills.subscription.bilibili_video import BilibiliVideoFetcher
from hypo_agent.skills.subscription.cookie_checker import CookieHealthResult, check_cookie_health
from hypo_agent.skills.subscription.resolver import ResolvedTarget, SubscriptionTargetResolver
from hypo_agent.skills.subscription.weibo import WeiboFetcher
from hypo_agent.skills.subscription.zhihu_pins import ZhihuPinsFetcher
from hypo_agent.utils.timeutil import to_local

logger = structlog.get_logger("hypo_agent.skills.subscription.manager")
_COOKIE_ALERT_INTERVAL = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


class SubscriptionManager:
    def __init__(
        self,
        *,
        structured_store: StructuredStore,
        scheduler: Any,
        message_queue: Any | None = None,
        heartbeat_service: Any | None = None,
        fetchers: dict[str, BaseFetcher] | None = None,
        target_resolver: Any | None = None,
        cookie_checker: Any | None = None,
        cookie_checker_transport: Any | None = None,
        secrets_path: Path | str = "config/secrets.yaml",
        default_session_id: str = "main",
    ) -> None:
        self.structured_store = structured_store
        self.scheduler = scheduler
        self.message_queue = message_queue
        self.heartbeat_service = heartbeat_service
        self.default_session_id = default_session_id
        self.secrets_path = Path(secrets_path)
        self.fetchers = fetchers or self._build_default_fetchers()
        self.target_resolver = target_resolver or self._build_target_resolver()
        self.cookie_checker = cookie_checker or check_cookie_health
        self.cookie_checker_transport = cookie_checker_transport
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._platform_cooldowns: dict[str, datetime] = {}
        self._recent_items: list[dict[str, Any]] = []
        if heartbeat_service is not None and hasattr(heartbeat_service, "register_event_source"):
            heartbeat_service.register_event_source("subscriptions", self.heartbeat_check)

    async def init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self.structured_store.init()
            async with aiosqlite.connect(self.structured_store.db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        id TEXT PRIMARY KEY,
                        platform TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        target_name TEXT,
                        fetcher_key TEXT NOT NULL,
                        poll_interval_sec INTEGER NOT NULL,
                        auth_profile_id TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        last_success_at TEXT,
                        last_checked_at TEXT,
                        last_error_code TEXT,
                        last_error_message TEXT,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        next_poll_at TEXT,
                        session_id TEXT NOT NULL DEFAULT 'main',
                        config_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(platform, target_id, fetcher_key)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscription_items (
                        id TEXT PRIMARY KEY,
                        subscription_id TEXT NOT NULL,
                        platform_item_id TEXT NOT NULL,
                        item_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        url TEXT NOT NULL,
                        author_id TEXT,
                        author_name TEXT,
                        published_at TEXT,
                        content_hash TEXT NOT NULL,
                        raw_json TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        notified_at TEXT,
                        UNIQUE(subscription_id, platform_item_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_subscription_items_subscription_published
                    ON subscription_items(subscription_id, published_at DESC)
                    """
                )
                await db.commit()
            self._initialized = True

    async def add_subscription(
        self,
        *,
        platform: str,
        target_id: str,
        name: str,
        interval_minutes: int | None = None,
        fetcher_key: str | None = None,
        session_id: str | None = None,
        bootstrap: bool = False,
    ) -> dict[str, Any]:
        await self.init()
        resolved_target = await self._resolve_subscription_target(
            platform=platform,
            target_id=target_id,
        )
        resolved_fetcher_key = self._resolve_fetcher_key(platform, fetcher_key)
        if resolved_fetcher_key not in self.fetchers:
            raise ValueError(f"unsupported fetcher '{resolved_fetcher_key}'")
        subscription_id = str(uuid.uuid4())
        now_iso = _utc_iso()
        poll_interval_sec = max(60, int((interval_minutes or 10) * 60))
        record = {
            "id": subscription_id,
            "platform": self._normalize_platform(platform),
            "target_id": resolved_target.target_id if resolved_target is not None else str(target_id).strip(),
            "target_name": str(name).strip(),
            "fetcher_key": resolved_fetcher_key,
            "poll_interval_sec": poll_interval_sec,
            "auth_profile_id": self._resolve_auth_profile_id(resolved_fetcher_key),
            "enabled": 1,
            "last_success_at": None,
            "last_checked_at": None,
            "last_error_code": None,
            "last_error_message": None,
            "consecutive_failures": 0,
            "next_poll_at": None,
            "session_id": str(session_id or self.default_session_id),
            "config_json": "{}",
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            try:
                await db.execute(
                    """
                    INSERT INTO subscriptions (
                        id, platform, target_id, target_name, fetcher_key, poll_interval_sec,
                        auth_profile_id, enabled, last_success_at, last_checked_at, last_error_code,
                        last_error_message, consecutive_failures, next_poll_at, session_id, config_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["platform"],
                        record["target_id"],
                        record["target_name"],
                        record["fetcher_key"],
                        record["poll_interval_sec"],
                        record["auth_profile_id"],
                        record["enabled"],
                        record["last_success_at"],
                        record["last_checked_at"],
                        record["last_error_code"],
                        record["last_error_message"],
                        record["consecutive_failures"],
                        record["next_poll_at"],
                        record["session_id"],
                        record["config_json"],
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise ValueError("subscription already exists") from exc
            await db.commit()
        self._register_job(record)
        if bootstrap:
            await self.poll_subscription(subscription_id, notify=False, bootstrap=True)
        return await self.get_subscription(subscription_id) or record

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            async with db.execute(
                """
                SELECT id, platform, target_id, target_name, fetcher_key, poll_interval_sec,
                       auth_profile_id, enabled, last_success_at, last_checked_at, last_error_code,
                       last_error_message, consecutive_failures, next_poll_at, session_id,
                       config_json, created_at, updated_at
                FROM subscriptions
                ORDER BY created_at ASC, id ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._subscription_row_to_dict(row) for row in rows]

    async def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            async with db.execute(
                """
                SELECT id, platform, target_id, target_name, fetcher_key, poll_interval_sec,
                       auth_profile_id, enabled, last_success_at, last_checked_at, last_error_code,
                       last_error_message, consecutive_failures, next_poll_at, session_id,
                       config_json, created_at, updated_at
                FROM subscriptions
                WHERE id = ?
                """,
                (subscription_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._subscription_row_to_dict(row) if row else None

    async def remove_subscription(self, subscription_id: str) -> bool:
        await self.init()
        self._remove_job(subscription_id)
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            cursor = await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
            await db.execute(
                "DELETE FROM subscription_items WHERE subscription_id = ?",
                (subscription_id,),
            )
            await db.commit()
        return bool(getattr(cursor, "rowcount", 0))

    async def restore_enabled_subscriptions(self) -> int:
        await self.init()
        restored = 0
        for subscription in await self.list_subscriptions():
            if subscription["enabled"]:
                self._register_job(subscription)
                restored += 1
        logger.info("subscription.restore.done", restored=restored)
        return restored

    async def poll_subscription(
        self,
        subscription_id: str,
        *,
        notify: bool = True,
        bootstrap: bool = False,
    ) -> dict[str, Any]:
        await self.init()
        subscription = await self.get_subscription(subscription_id)
        if subscription is None:
            raise ValueError("subscription not found")
        result, _, _, _ = await self._execute_subscription_check(
            subscription,
            notify=notify,
            bootstrap=bootstrap,
            emit_alerts=True,
        )
        return result

    async def check_subscriptions(self, subscription_id: str | None = None) -> dict[str, Any]:
        await self.init()
        results: list[dict[str, Any]] = []
        scoped_subscriptions: list[dict[str, Any]] = []
        if subscription_id:
            subscription = await self.get_subscription(subscription_id)
            if subscription is None:
                raise ValueError("subscription not found")
            scoped_subscriptions = [subscription]
            checked = await self.poll_subscription(subscription_id)
            results.append(await self._attach_recent_known_items(checked, subscription=subscription))
        else:
            scoped_subscriptions = [item for item in await self.list_subscriptions() if item["enabled"]]
            for subscription in scoped_subscriptions:
                checked = await self.poll_subscription(subscription["id"])
                results.append(await self._attach_recent_known_items(checked, subscription=subscription))
        cookie_health, cookie_health_summary = await self.get_cookie_health(
            subscriptions=scoped_subscriptions,
            active_only=False,
        )
        response = {
            "checked": len(results),
            "new_items": sum(int(item.get("new_items", 0) or 0) for item in results),
            "items": results,
            "cookie_health": cookie_health,
            "cookie_health_summary": cookie_health_summary,
        }
        if len(results) == 1:
            response["recent_known_items"] = list(results[0].get("recent_known_items") or [])
            response["latest_known_item"] = results[0].get("latest_known_item")
        response["summary"] = self._format_check_summary(response)
        return response

    async def get_status(self) -> dict[str, Any]:
        await self.init()
        subscriptions = await self.list_subscriptions()
        active = [item for item in subscriptions if item["enabled"]]
        recent_errors = [
            {
                "id": item["id"],
                "name": item["target_name"],
                "error_code": item.get("last_error_code"),
                "error_message": item.get("last_error_message"),
            }
            for item in subscriptions
            if item.get("last_error_code")
        ][:5]
        next_poll = None
        for item in active:
            scheduled = self.scheduler.get_job_next_run_iso(self._job_id(item["id"]))
            if scheduled and (next_poll is None or scheduled < next_poll):
                next_poll = scheduled
        cookie_health, cookie_health_summary = await self.get_cookie_health(subscriptions=active, active_only=False)
        cookie_status_lines = self._format_cookie_status_lines(active, cookie_health)
        response = {
            "total": len(subscriptions),
            "active": len(active),
            "recent_errors": recent_errors,
            "next_poll_at": next_poll,
            "cookie_health": cookie_health,
            "cookie_health_summary": cookie_health_summary,
            "cookie_status_lines": cookie_status_lines,
        }
        response["summary"] = self._format_status_summary(response)
        return response

    async def heartbeat_snapshot(self) -> dict[str, Any]:
        active = [item for item in await self.list_subscriptions() if item["enabled"]]
        cookie_health, cookie_health_summary = await self.get_cookie_health(subscriptions=active, active_only=False)
        return {
            "name": "subscriptions",
            "new_items": len(self._recent_items),
            "items": list(self._recent_items[:6]),
            "cookie_health": cookie_health,
            "cookie_health_summary": cookie_health_summary,
            "alerts": [
                f"{item['display_name']}\uff1a{item['message']}"
                for item in cookie_health
                if item.get("status") in {"invalid", "error"}
            ],
        }

    async def heartbeat_check(self) -> str | None:
        try:
            await self.init()
            active = [item for item in await self.list_subscriptions() if item["enabled"]]
            if not active:
                return None
            cookie_health, _ = await self.get_cookie_health(subscriptions=active, active_only=False)
            cookie_health_by_platform = {
                self._normalize_platform(str(item.get("platform") or "")): item
                for item in cookie_health
            }
            subscriptions_by_platform = self._group_subscriptions_by_platform(active)
            for platform, platform_subscriptions in subscriptions_by_platform.items():
                health = cookie_health_by_platform.get(platform)
                if self._cookie_health_requires_alert(health):
                    await self._maybe_send_cookie_alert(
                        platform=platform,
                        subscriptions=platform_subscriptions,
                        health=health,
                    )
            summaries: list[str] = []
            for subscription in active:
                try:
                    health = cookie_health_by_platform.get(
                        self._normalize_platform(str(subscription.get("platform") or ""))
                    )
                    if self._should_skip_heartbeat_subscription(subscription, health=health):
                        logger.info(
                            "subscription.skip_no_cookie",
                            subscription_id=subscription.get("id"),
                            platform=subscription.get("platform"),
                            source="heartbeat",
                        )
                        continue
                    result, new_items, _, checked_subscription = await self._execute_subscription_check(
                        subscription,
                        notify=False,
                        bootstrap=False,
                        emit_alerts=False,
                    )
                    if result.get("status") == "error" and str(result.get("error_code") or "") == "auth_stale":
                        await self._maybe_send_cookie_alert(
                            platform=str(checked_subscription.get("platform") or ""),
                            subscriptions=[checked_subscription],
                            health=health,
                            reason="auth_stale",
                        )
                        continue
                    for item in new_items[:3]:
                        self._record_recent_item(checked_subscription, item)
                        summaries.append(self._format_heartbeat_item(checked_subscription, item))
                except Exception as exc:
                    logger.warning(
                        "subscription.heartbeat_error",
                        subscription_id=subscription.get("id"),
                        platform=subscription.get("platform"),
                        error=str(exc),
                    )
            if not summaries:
                return None
            return "\U0001F4E2 \u8ba2\u9605\u66f4\u65b0:\n" + "\n\n".join(summaries[:3])
        except Exception as exc:
            logger.warning(
                "subscription.heartbeat_error",
                subscription_id=None,
                platform=None,
                error=str(exc),
            )
            return None

    async def get_cookie_health(
        self,
        *,
        subscriptions: list[dict[str, Any]] | None = None,
        active_only: bool = True,
    ) -> tuple[list[dict[str, Any]], str]:
        await self.init()
        scoped_subscriptions = list(subscriptions) if subscriptions is not None else await self.list_subscriptions()
        if active_only:
            scoped_subscriptions = [item for item in scoped_subscriptions if item["enabled"]]
        platforms = self._collect_subscription_platforms(scoped_subscriptions)
        results: list[CookieHealthResult] = []
        for platform in platforms:
            cookie = self._load_platform_cookie(platform)
            try:
                health = await self.cookie_checker(
                    platform,
                    cookie,
                    transport=self.cookie_checker_transport,
                )
            except Exception as exc:
                health = CookieHealthResult(
                    platform=platform,
                    valid=False,
                    error=str(exc),
                    config_path=self._cookie_config_path(platform),
                    message=f"Cookie \u68c0\u67e5\u5931\u8d25\uff08{exc}\uff09",
                )
            results.append(health)
        serialized = [await self._serialize_cookie_health(item) for item in results]
        return serialized, self._format_cookie_health_summary(results)

    @staticmethod
    def classify_failure_payload(payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]:
        if isinstance(payload, Exception):
            return ("network", True, False)
        code = int(payload.get("code", 0) or 0)
        if code == -101:
            return ("auth_stale", False, True)
        if code in {-352, -412}:
            return ("anti_bot", True, False)
        return ("schema_changed", False, False)

    def _build_default_fetchers(self) -> dict[str, BaseFetcher]:
        bilibili_cookie = self._load_service_cookie("bilibili")
        weibo_cookie = self._load_service_cookie("weibo")
        fetchers: dict[str, BaseFetcher] = {
            "zhihu_pins": ZhihuPinsFetcher(),
        }
        if bilibili_cookie:
            fetchers["bilibili_video"] = BilibiliVideoFetcher(cookie=bilibili_cookie)
            fetchers["bilibili_dynamic"] = BilibiliDynamicFetcher(cookie=bilibili_cookie)
        if weibo_cookie:
            fetchers["weibo"] = WeiboFetcher(cookie=weibo_cookie)
        return fetchers

    def _build_target_resolver(self) -> SubscriptionTargetResolver:
        return SubscriptionTargetResolver(
            bilibili_cookie=self._load_service_cookie("bilibili"),
            weibo_cookie=self._load_service_cookie("weibo"),
            zhihu_cookie=self._load_service_cookie("zhihu"),
        )

    def _load_service_cookie(self, service_name: str) -> str:
        try:
            secrets = load_secrets_config(self.secrets_path)
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning("subscription.secrets.load_failed", path=str(self.secrets_path), error=str(exc))
            return ""
        services = getattr(secrets, "services", None)
        service = getattr(services, service_name, None)
        return str(getattr(service, "cookie", "") or "").strip()

    def _load_platform_cookie(self, platform: str) -> str:
        normalized = self._normalize_platform(platform)
        if normalized == "bilibili":
            return self._load_service_cookie("bilibili")
        if normalized == "weibo":
            return self._load_service_cookie("weibo")
        if normalized == "zhihu_pins":
            return self._load_service_cookie("zhihu")
        return ""

    def _register_job(self, subscription: dict[str, Any]) -> None:
        if not hasattr(self.scheduler, "register_subscription_job"):
            return
        self.scheduler.register_subscription_job(
            self._job_id(subscription["id"]),
            lambda: self.poll_subscription(subscription["id"]),
            interval_seconds=int(subscription["poll_interval_sec"]),
            jitter_seconds=60,
        )
        logger.info(
            "subscription.job.registered",
            subscription_id=subscription["id"],
            interval_seconds=subscription["poll_interval_sec"],
        )

    def _remove_job(self, subscription_id: str) -> None:
        if hasattr(self.scheduler, "remove_subscription_job"):
            self.scheduler.remove_subscription_job(self._job_id(subscription_id))

    def _job_id(self, subscription_id: str) -> str:
        return f"subscription:{subscription_id}"

    def _record_recent_item(self, subscription: dict[str, Any], item: NormalizedItem) -> None:
        self._recent_items.insert(
            0,
            {
                "subscription_id": subscription["id"],
                "subscription": subscription.get("target_name") or subscription.get("target_id"),
                "platform": subscription.get("platform"),
                "author_name": item.author_name,
                "title": item.title,
                "url": item.url,
                "published_at": to_local(item.published_at).isoformat() if item.published_at else None,
            },
        )
        self._recent_items = self._recent_items[:20]

    def _should_skip_heartbeat_subscription(
        self,
        subscription: dict[str, Any],
        *,
        health: dict[str, Any] | None = None,
    ) -> bool:
        if health is not None and self._cookie_health_requires_skip(health):
            return True
        return not bool(self._load_platform_cookie(str(subscription.get("platform") or "")))

    def _format_heartbeat_item(self, subscription: dict[str, Any], item: NormalizedItem) -> str:
        platform_name = self._cookie_platform_display_name(str(subscription.get("platform") or ""))
        author_name = item.author_name or str(subscription.get("target_name") or subscription.get("target_id") or "")
        return f"\U0001F4FA [{platform_name}] {author_name} - {item.title}\n{item.url}"

    def _group_subscriptions_by_platform(
        self,
        subscriptions: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for subscription in subscriptions:
            platform = self._normalize_platform(str(subscription.get("platform") or ""))
            grouped.setdefault(platform, []).append(subscription)
        return grouped

    def _cookie_alert_preference_key(self, platform: str) -> str:
        return f"cookie_alert_last_{self._normalize_platform(platform)}"

    async def _get_cookie_alert_last(self, platform: str) -> datetime | None:
        raw = await self.structured_store.get_preference(self._cookie_alert_preference_key(platform))
        return self._parse_datetime(raw)

    async def _set_cookie_alert_last(self, platform: str, value: datetime) -> None:
        await self.structured_store.set_preference(
            self._cookie_alert_preference_key(platform),
            value.isoformat(),
        )

    def _cookie_health_requires_skip(self, health: dict[str, Any] | None) -> bool:
        return bool(isinstance(health, dict) and str(health.get("status") or "").strip() == "invalid")

    def _cookie_health_requires_alert(self, health: dict[str, Any] | None) -> bool:
        if not isinstance(health, dict):
            return False
        return str(health.get("error") or "").strip() in {"missing_cookie", "unauthenticated"}

    async def _maybe_send_cookie_alert(
        self,
        *,
        platform: str,
        subscriptions: list[dict[str, Any]],
        health: dict[str, Any] | None,
        reason: str | None = None,
    ) -> bool:
        normalized_platform = self._normalize_platform(platform)
        if not subscriptions:
            return False
        last_alert_at = await self._get_cookie_alert_last(normalized_platform)
        now = _utc_now()
        if last_alert_at is not None and now - last_alert_at < _COOKIE_ALERT_INTERVAL:
            logger.info(
                "subscription.cookie_alert.skipped_recent",
                platform=normalized_platform,
                last_alert_at=last_alert_at.isoformat(),
                reason=reason or str((health or {}).get("error") or ""),
            )
            return False
        message = self._format_cookie_alert_message(
            platform=normalized_platform,
            subscriptions=subscriptions,
            health=health,
            reason=reason,
        )
        await self._push_text(subscriptions[0], message)
        await self._set_cookie_alert_last(normalized_platform, now)
        logger.warning(
            "subscription.cookie_alert.sent",
            platform=normalized_platform,
            subscription_count=len(subscriptions),
            reason=reason or str((health or {}).get("error") or ""),
        )
        return True

    def _format_cookie_alert_message(
        self,
        *,
        platform: str,
        subscriptions: list[dict[str, Any]],
        health: dict[str, Any] | None,
        reason: str | None,
    ) -> str:
        issue = "Cookie \u5df2\u5931\u6548"
        if str((health or {}).get("error") or "").strip() == "missing_cookie":
            issue = "Cookie \u7f3a\u5931"
        if str(reason or "").strip() == "auth_stale":
            issue = "Cookie \u5df2\u5931\u6548"
        config_path = self._cookie_config_path(platform) or f"{platform}.cookie"
        return (
            f"\u26a0\ufe0f {self._cookie_platform_display_name(platform)} {issue}\uff0c"
            f"{self._cookie_alert_target_label(subscriptions)}\u6682\u65f6\u65e0\u6cd5\u66f4\u65b0\u3002"
            f"\u8bf7\u66f4\u65b0 config/secrets.yaml \u4e2d\u7684 {config_path} \u5e76\u91cd\u542f\u670d\u52a1\u3002"
        )

    def _cookie_alert_target_label(self, subscriptions: list[dict[str, Any]]) -> str:
        names = [
            str(item.get("target_name") or item.get("target_id") or "").strip()
            for item in subscriptions
            if str(item.get("target_name") or item.get("target_id") or "").strip()
        ]
        unique_names = list(dict.fromkeys(names))
        if not unique_names:
            return "\u8fd9\u4e9b\u8ba2\u9605"
        if len(unique_names) == 1 and len(subscriptions) == 1:
            return f'"{unique_names[0]}"'
        return f'"{unique_names[0]}"\u7b49\u8ba2\u9605'

    def _platform_status_icon(self, platform: str) -> str:
        normalized = self._normalize_platform(platform)
        if normalized == "bilibili":
            return "\U0001F4FA"
        if normalized == "weibo":
            return "\U0001F4F1"
        if normalized == "zhihu_pins":
            return "\U0001F4D8"
        return "\U0001F4CC"

    def _format_cookie_status_lines(
        self,
        subscriptions: list[dict[str, Any]],
        cookie_health: list[dict[str, Any]],
    ) -> list[str]:
        grouped = self._group_subscriptions_by_platform(subscriptions)
        health_by_platform = {
            self._normalize_platform(str(item.get("platform") or "")): item
            for item in cookie_health
        }
        lines: list[str] = []
        for platform, platform_subscriptions in grouped.items():
            state_text = self._cookie_status_text(health_by_platform.get(platform))
            lines.append(
                f"{self._platform_status_icon(platform)} {self._cookie_platform_display_name(platform)}\u8ba2\u9605 "
                f"({len(platform_subscriptions)}\u4e2a): {state_text}"
            )
        return lines

    def _cookie_status_text(self, health: dict[str, Any] | None) -> str:
        if not isinstance(health, dict):
            return "\u26a0\ufe0f Cookie \u72b6\u6001\u672a\u77e5"
        status = str(health.get("status") or "").strip()
        if status == "valid":
            return "\u2705 Cookie \u6709\u6548"
        if status == "not_required":
            return "\u2139\ufe0f \u65e0\u9700 Cookie"
        if status == "invalid":
            last_alert_at = self._parse_datetime(health.get("last_alert_at"))
            if last_alert_at is not None:
                return (
                    "\u274c Cookie \u5df2\u5931\u6548\uff08\u4e0a\u6b21\u544a\u8b66: "
                    f"{to_local(last_alert_at).strftime('%Y-%m-%d %H:%M')}\uff09"
                )
            return "\u274c Cookie \u5df2\u5931\u6548"
        return "\u26a0\ufe0f Cookie \u68c0\u67e5\u5931\u8d25"

    async def _list_recent_items(self, subscription_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            async with db.execute(
                """
                SELECT platform_item_id, content_hash
                FROM subscription_items
                WHERE subscription_id = ?
                ORDER BY COALESCE(published_at, first_seen_at) DESC
                LIMIT ?
                """,
                (subscription_id, int(limit)),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "platform_item_id": row[0],
                "content_hash": row[1],
            }
            for row in rows
        ]

    async def _list_recent_item_details(
        self,
        subscription_id: str,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            async with db.execute(
                """
                SELECT title, url, published_at
                FROM subscription_items
                WHERE subscription_id = ?
                ORDER BY COALESCE(published_at, first_seen_at) DESC
                LIMIT ?
                """,
                (subscription_id, int(limit)),
            ) as cursor:
                rows = await cursor.fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            published_at = self._parse_datetime(row[2])
            items.append(
                {
                    "title": str(row[0] or "").strip(),
                    "url": str(row[1] or "").strip(),
                    "published_at": to_local(published_at).isoformat() if published_at else None,
                }
            )
        return items

    async def _attach_recent_known_items(
        self,
        result: dict[str, Any],
        *,
        subscription: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(result)
        recent_known_items = await self._list_recent_item_details(str(subscription.get("id") or ""), limit=5)
        enriched["subscription_name"] = str(
            subscription.get("target_name") or subscription.get("name") or subscription.get("target_id") or ""
        ).strip()
        enriched["platform"] = str(subscription.get("platform") or "").strip()
        enriched["recent_known_items"] = recent_known_items
        enriched["latest_known_item"] = recent_known_items[0] if recent_known_items else None
        return enriched

    async def _store_items(
        self,
        subscription_id: str,
        items: list[NormalizedItem],
        *,
        notify: bool,
    ) -> None:
        now_iso = _utc_iso()
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            for item in items:
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO subscription_items (
                        id, subscription_id, platform_item_id, item_type, title, summary, url,
                        author_id, author_name, published_at, content_hash, raw_json,
                        first_seen_at, last_seen_at, notified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{subscription_id}:{item.item_id}",
                        subscription_id,
                        item.item_id,
                        item.item_type,
                        item.title,
                        item.summary,
                        item.url,
                        item.author_id,
                        item.author_name,
                        item.published_at.isoformat() if item.published_at else None,
                        item.content_hash,
                        json.dumps(item.raw_payload, ensure_ascii=False),
                        now_iso,
                        now_iso,
                        now_iso if notify else None,
                    ),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) == 0:
                    await db.execute(
                        """
                        UPDATE subscription_items
                        SET last_seen_at = ?, notified_at = COALESCE(notified_at, ?)
                        WHERE subscription_id = ? AND platform_item_id = ?
                        """,
                        (
                            now_iso,
                            now_iso if notify else None,
                            subscription_id,
                            item.item_id,
                        ),
                    )
            await db.commit()

    async def _mark_success(self, subscription_id: str) -> None:
        now_iso = _utc_iso()
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            await db.execute(
                """
                UPDATE subscriptions
                SET last_success_at = ?, last_checked_at = ?, last_error_code = NULL,
                    last_error_message = NULL, consecutive_failures = 0, next_poll_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso, now_iso, now_iso, subscription_id),
            )
            await db.commit()

    async def _execute_subscription_check(
        self,
        subscription: dict[str, Any],
        *,
        notify: bool,
        bootstrap: bool,
        emit_alerts: bool,
    ) -> tuple[dict[str, Any], list[NormalizedItem], BaseFetcher | None, dict[str, Any]]:
        subscription_id = str(subscription.get("id") or "")
        fetcher = self.fetchers.get(str(subscription.get("fetcher_key") or ""))
        if not subscription["enabled"]:
            return (
                {"subscription_id": subscription_id, "status": "disabled", "new_items": 0},
                [],
                fetcher,
                subscription,
            )
        cooldown_until = self._platform_cooldowns.get(subscription["platform"])
        if cooldown_until is not None and cooldown_until > _utc_now():
            return (
                {
                    "subscription_id": subscription_id,
                    "status": "cooldown",
                    "new_items": 0,
                    "next_retry_at": to_local(cooldown_until).isoformat(),
                },
                [],
                fetcher,
                subscription,
            )
        next_poll_at = self._parse_datetime(subscription.get("next_poll_at"))
        if next_poll_at is not None and next_poll_at > _utc_now():
            return (
                {
                    "subscription_id": subscription_id,
                    "status": "backoff",
                    "new_items": 0,
                    "next_retry_at": to_local(next_poll_at).isoformat(),
                },
                [],
                fetcher,
                subscription,
            )
        if fetcher is None:
            raise ValueError(f"fetcher not found: {subscription.get('fetcher_key')}")

        checked_subscription = await self._repair_subscription_target(subscription)
        result = await fetcher.fetch_latest(checked_subscription)
        if not result.ok:
            await self._handle_fetch_error(
                checked_subscription,
                result,
                emit_alerts=emit_alerts,
            )
            updated = await self.get_subscription(subscription_id)
            return (
                {
                    "subscription_id": subscription_id,
                    "status": "error",
                    "error_code": result.error_code,
                    "enabled": updated["enabled"] if updated else False,
                    "new_items": 0,
                },
                [],
                fetcher,
                checked_subscription,
            )

        stored_items = await self._list_recent_items(subscription_id, limit=100)
        new_items = fetcher.diff(stored_items, result.items)
        await self._store_items(subscription_id, result.items, notify=False)
        await self._mark_success(subscription_id)
        if notify and not bootstrap:
            for item in new_items:
                await self._push_item(checked_subscription, fetcher, item)
        logger.info(
            "subscription.diff.done",
            subscription_id=subscription_id,
            fetched_count=len(result.items),
            new_count=len(new_items),
            bootstrap=bootstrap,
            notify=notify,
            source="scheduler" if notify else "heartbeat",
        )
        return (
            {
                "subscription_id": subscription_id,
                "status": "ok",
                "fetched_items": len(result.items),
                "new_items": len(new_items),
            },
            new_items,
            fetcher,
            checked_subscription,
        )

    async def _handle_fetch_error(
        self,
        subscription: dict[str, Any],
        result: FetchResult,
        *,
        emit_alerts: bool = True,
    ) -> None:
        now = _utc_now()
        failures = int(subscription.get("consecutive_failures", 0) or 0) + 1
        enabled = bool(subscription.get("enabled", True))
        next_poll_at: datetime | None = None
        alert_message: str | None = None
        if result.error_code == "auth_stale" and failures >= 3:
            enabled = False
            alert_message = (
                f"Subscription paused: {subscription.get('target_name') or subscription.get('target_id')} "
                "hit 3 consecutive auth failures. Update the Bilibili cookie."
            )
        elif result.error_code == "anti_bot" and failures >= 5:
            next_poll_at = now + timedelta(hours=6)
            self._platform_cooldowns[str(subscription.get("platform") or "")] = next_poll_at
            alert_message = (
                f"Platform cooldown entered: {subscription.get('platform')} "
                f"hit {failures} consecutive anti-bot failures."
            )
        elif failures >= 10:
            next_poll_at = now + timedelta(hours=24)
        logger.warning(
            "subscription.fetch.failed",
            subscription_id=subscription.get("id"),
            error_code=result.error_code,
            retryable=result.retryable,
            auth_stale=result.auth_stale,
            consecutive_failures=failures,
            enabled=enabled,
            next_poll_at=next_poll_at.isoformat() if next_poll_at else None,
        )
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            await db.execute(
                """
                UPDATE subscriptions
                SET enabled = ?, last_checked_at = ?, last_error_code = ?, last_error_message = ?,
                    consecutive_failures = ?, next_poll_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if enabled else 0,
                    now.isoformat(),
                    result.error_code,
                    result.error_message,
                    failures,
                    next_poll_at.isoformat() if next_poll_at else None,
                    now.isoformat(),
                    subscription["id"],
                ),
            )
            await db.commit()
        if not enabled:
            self._remove_job(subscription["id"])
        if emit_alerts and alert_message:
            await self._push_text(subscription, alert_message)

    async def _push_item(
        self,
        subscription: dict[str, Any],
        fetcher: BaseFetcher,
        item: NormalizedItem,
    ) -> None:
        await self._store_items(subscription["id"], [item], notify=True)
        message = fetcher.format_notification(item)
        await self._push_text(subscription, message, item=item)
        self._record_recent_item(subscription, item)
        logger.info(
            "subscription.push.sent",
            subscription_id=subscription["id"],
            item_id=item.item_id,
            item_type=item.item_type,
        )

    async def _push_text(
        self,
        subscription: dict[str, Any],
        text: str,
        *,
        item: NormalizedItem | None = None,
    ) -> None:
        if self.message_queue is None:
            return
        await self.message_queue.put(
            {
                "event_type": "subscription_trigger",
                "session_id": str(subscription.get("session_id") or self.default_session_id),
                "summary": text,
                "subscription_id": subscription.get("id"),
                "platform": subscription.get("platform"),
                "target_id": subscription.get("target_id"),
                "item_id": item.item_id if item is not None else None,
            }
        )

    def _subscription_row_to_dict(self, row: Any) -> dict[str, Any]:
        (
            subscription_id,
            platform,
            target_id,
            target_name,
            fetcher_key,
            poll_interval_sec,
            auth_profile_id,
            enabled,
            last_success_at,
            last_checked_at,
            last_error_code,
            last_error_message,
            consecutive_failures,
            next_poll_at,
            session_id,
            config_json,
            created_at,
            updated_at,
        ) = row
        return {
            "id": subscription_id,
            "platform": platform,
            "target_id": target_id,
            "target_name": target_name,
            "name": target_name,
            "fetcher_key": fetcher_key,
            "poll_interval_sec": int(poll_interval_sec or 600),
            "auth_profile_id": auth_profile_id,
            "enabled": bool(enabled),
            "last_success_at": last_success_at,
            "last_checked_at": last_checked_at,
            "last_error_code": last_error_code,
            "last_error_message": last_error_message,
            "consecutive_failures": int(consecutive_failures or 0),
            "next_poll_at": next_poll_at,
            "session_id": session_id,
            "config": json.loads(config_json or "{}"),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _resolve_fetcher_key(self, platform: str, fetcher_key: str | None) -> str:
        cleaned = str(fetcher_key or "").strip()
        if cleaned:
            return cleaned
        normalized_platform = self._normalize_platform(platform)
        if normalized_platform == "bilibili":
            return "bilibili_video"
        return normalized_platform

    async def _resolve_subscription_target(
        self,
        *,
        platform: str,
        target_id: str,
    ) -> ResolvedTarget | None:
        normalized_platform = self._normalize_platform(platform)
        cleaned_target_id = str(target_id or "").strip()
        if not self._target_id_requires_resolution(normalized_platform, cleaned_target_id):
            return None
        resolver = self.target_resolver
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            return None
        try:
            resolved = await resolver.resolve(normalized_platform, cleaned_target_id)
        except Exception as exc:
            logger.warning(
                "subscription.target.resolve_failed",
                platform=normalized_platform,
                query=cleaned_target_id,
                error=str(exc),
            )
            return None
        return resolved

    async def _repair_subscription_target(self, subscription: dict[str, Any]) -> dict[str, Any]:
        resolved = await self._resolve_subscription_target(
            platform=str(subscription.get("platform") or ""),
            target_id=str(subscription.get("target_id") or ""),
        )
        if resolved is None:
            return subscription
        await self._update_subscription_target_id(
            subscription_id=str(subscription.get("id") or ""),
            target_id=resolved.target_id,
        )
        repaired = dict(subscription)
        repaired["target_id"] = resolved.target_id
        logger.info(
            "subscription.target.repaired",
            subscription_id=subscription.get("id"),
            platform=subscription.get("platform"),
            old_target_id=subscription.get("target_id"),
            new_target_id=resolved.target_id,
            canonical_name=resolved.canonical_name,
        )
        return repaired

    async def _update_subscription_target_id(self, *, subscription_id: str, target_id: str) -> None:
        now_iso = _utc_iso()
        async with aiosqlite.connect(self.structured_store.db_path) as db:
            await db.execute(
                """
                UPDATE subscriptions
                SET target_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(target_id).strip(), now_iso, subscription_id),
            )
            await db.commit()

    def _resolve_auth_profile_id(self, fetcher_key: str) -> str | None:
        cleaned = str(fetcher_key or "").strip()
        if cleaned.startswith("bilibili_"):
            return "services.bilibili.cookie"
        if cleaned == "weibo":
            return "services.weibo.cookie"
        return None

    def _normalize_platform(self, platform: str) -> str:
        raw = str(platform or "").strip().lower()
        if raw.startswith("bilibili"):
            return "bilibili"
        if raw in {"zhihu", "zhihu_pins"}:
            return "zhihu_pins"
        return raw

    def _target_id_requires_resolution(self, platform: str, target_id: str) -> bool:
        cleaned = str(target_id or "").strip()
        if not cleaned:
            return False
        if platform in {"bilibili", "weibo"}:
            return not cleaned.isdigit()
        if platform == "zhihu_pins":
            return not cleaned.replace("-", "").replace("_", "").isalnum()
        return False

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def _collect_subscription_platforms(self, subscriptions: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        platforms: list[str] = []
        for item in subscriptions:
            platform = self._normalize_platform(str(item.get("platform") or item.get("fetcher_key") or ""))
            if not platform or platform in seen:
                continue
            seen.add(platform)
            platforms.append(platform)
        return platforms

    def _cookie_config_path(self, platform: str) -> str | None:
        normalized = self._normalize_platform(platform)
        if normalized == "bilibili":
            return "services.bilibili.cookie"
        if normalized == "weibo":
            return "services.weibo.cookie"
        return None

    def _cookie_health_status(self, result: CookieHealthResult) -> str:
        if not result.needs_cookie:
            return "not_required"
        if result.valid:
            return "valid"
        if result.error in {"missing_cookie", "unauthenticated"}:
            return "invalid"
        return "error"

    def _cookie_health_message(self, result: CookieHealthResult) -> str:
        if result.message:
            return result.message
        if not result.needs_cookie:
            return "\u65e0\u9700 Cookie"
        if result.valid:
            if result.username:
                return f"Cookie \u6709\u6548\uff08\u767b\u5f55\u7528\u6237\uff1a{result.username}\uff09"
            return "Cookie \u6709\u6548"
        if result.error == "missing_cookie":
            config_path = result.config_path or self._cookie_config_path(result.platform)
            if config_path:
                return f"\u672a\u914d\u7f6e Cookie\uff0c\u8bf7\u66f4\u65b0 secrets.yaml \u4e2d\u7684 {config_path}"
            return "\u672a\u914d\u7f6e Cookie"
        if result.error == "unauthenticated":
            config_path = result.config_path or self._cookie_config_path(result.platform)
            if config_path:
                return (
                    "Cookie \u5df2\u5931\u6548\uff08\u672a\u767b\u5f55\u6001\uff0c"
                    f"\u8bf7\u66f4\u65b0 secrets.yaml \u4e2d\u7684 {config_path}\uff09"
                )
            return "Cookie \u5df2\u5931\u6548\uff08\u672a\u767b\u5f55\u6001\uff09"
        if result.error:
            return f"Cookie \u68c0\u67e5\u5931\u8d25\uff08{result.error}\uff09"
        return "Cookie \u72b6\u6001\u672a\u77e5"

    def _cookie_platform_display_name(self, platform: str) -> str:
        normalized = self._normalize_platform(platform)
        if normalized == "bilibili":
            return "B\u7ad9"
        if normalized == "weibo":
            return "\u5fae\u535a"
        if normalized == "zhihu_pins":
            return "\u77e5\u4e4e"
        return normalized or platform

    async def _serialize_cookie_health(self, result: CookieHealthResult) -> dict[str, Any]:
        status = self._cookie_health_status(result)
        last_alert_at = await self._get_cookie_alert_last(result.platform)
        return {
            "platform": result.platform,
            "display_name": self._cookie_platform_display_name(result.platform),
            "status": status,
            "valid": result.valid,
            "needs_cookie": result.needs_cookie,
            "username": result.username,
            "error": result.error,
            "config_path": result.config_path or self._cookie_config_path(result.platform),
            "message": self._cookie_health_message(result),
            "last_alert_at": last_alert_at.isoformat() if last_alert_at is not None else None,
        }

    def _format_cookie_health_summary(self, results: list[CookieHealthResult]) -> str:
        if not results:
            return "Cookie \u72b6\u6001\uff1a\n  \u2139\ufe0f \u6682\u65e0\u9700\u8981\u68c0\u67e5\u7684\u8ba2\u9605\u5e73\u53f0"
        has_issue = any(self._cookie_health_status(item) in {"invalid", "error"} for item in results)
        lines = ["\u26a0\ufe0f Cookie \u72b6\u6001\uff1a" if has_issue else "Cookie \u72b6\u6001\uff1a"]
        for item in results:
            status = self._cookie_health_status(item)
            icon = (
                "\u2139\ufe0f"
                if status == "not_required"
                else "\u2705"
                if status == "valid"
                else "\u274c"
                if status == "invalid"
                else "\u26a0\ufe0f"
            )
            lines.append(
                f"  {icon} {self._cookie_platform_display_name(item.platform)}\uff1a{self._cookie_health_message(item)}"
            )
        return "\n".join(lines)

    def _format_check_summary(self, result: dict[str, Any]) -> str:
        headline = (
            f"\u68c0\u67e5\u5b8c\u6210\uff1a\u5171 {int(result.get('checked', 0) or 0)} "
            f"\u4e2a\u8ba2\u9605\uff0c\u65b0\u589e\u5185\u5bb9 {int(result.get('new_items', 0) or 0)}"
        )
        cookie_summary = str(result.get("cookie_health_summary") or "").strip()
        if not cookie_summary:
            return headline
        return f"{headline}\n\n{cookie_summary}"

    def _format_status_summary(self, result: dict[str, Any]) -> str:
        headline = (
            f"\u5f53\u524d\u5171 {int(result.get('total', 0) or 0)} "
            f"\u4e2a\u8ba2\u9605\uff0c\u6d3b\u8dc3 {int(result.get('active', 0) or 0)} \u4e2a"
        )
        cookie_status_lines = result.get("cookie_status_lines")
        if isinstance(cookie_status_lines, list) and cookie_status_lines:
            return f"{headline}\n\n" + "\n".join(
                str(line).strip() for line in cookie_status_lines if str(line).strip()
            )
        cookie_summary = str(result.get("cookie_health_summary") or "").strip()
        if not cookie_summary:
            return headline
        return f"{headline}\n\n{cookie_summary}"
