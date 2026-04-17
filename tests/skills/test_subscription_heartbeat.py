from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

import hypo_agent.skills.subscription.manager as manager_module
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem
from hypo_agent.skills.subscription.cookie_checker import CookieHealthResult
from hypo_agent.skills.subscription.manager import SubscriptionManager


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class DummyHeartbeat:
    def __init__(self) -> None:
        self.sources: dict[str, Any] = {}

    def register_event_source(self, name: str, callback: Any) -> None:
        self.sources[name] = callback


class DummyScheduler:
    def register_subscription_job(
        self,
        job_id: str,
        coro: Any,
        *,
        interval_seconds: int,
        jitter_seconds: int,
    ) -> None:
        del job_id, coro, interval_seconds, jitter_seconds

    def remove_subscription_job(self, job_id: str) -> None:
        del job_id

    def get_job_next_run_iso(self, job_id: str) -> str | None:
        del job_id
        return None


class RecordingLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict[str, Any]]] = []
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_calls.append((event, kwargs))


class StubFetcher:
    platform = "bilibili_video"

    def __init__(self, results: list[FetchResult] | None = None, *, raises: Exception | None = None) -> None:
        self.results = list(results or [])
        self.raises = raises
        self.calls = 0

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        del subscription
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        if self.results:
            return self.results.pop(0)
        return FetchResult(ok=True, items=[])

    def diff(
        self,
        stored_items: list[dict[str, Any]],
        fetched_items: list[NormalizedItem],
    ) -> list[NormalizedItem]:
        stored_ids = {str(row.get("platform_item_id") or "") for row in stored_items}
        return [item for item in fetched_items if item.item_id not in stored_ids]

    def format_notification(self, item: NormalizedItem) -> str:
        return f"notify:{item.title}"

    def classify_error(self, payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]:
        del payload
        return ("network", True, False)


def _item(item_id: str, title: str) -> NormalizedItem:
    return NormalizedItem.from_payload(
        platform="bilibili",
        subscription_id="sub-1",
        item_id=item_id,
        item_type="video",
        title=title,
        summary=title,
        url=f"https://example.com/{item_id}",
        author_id="546195",
        author_name="author-demo",
        published_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
        raw_payload={"id": item_id},
    )


def _write_secrets(
    path: Path,
    *,
    bilibili_cookie: str = "",
    weibo_cookie: str = "",
    zhihu_cookie: str = "",
) -> None:
    path.write_text(
        "\n".join(
            [
                "providers: {}",
                "services:",
                "  bilibili:",
                f"    cookie: {bilibili_cookie}" if bilibili_cookie else "    cookie: ''",
                "  weibo:",
                f"    cookie: {weibo_cookie}" if weibo_cookie else "    cookie: ''",
                "  zhihu:",
                f"    cookie: {zhihu_cookie}" if zhihu_cookie else "    cookie: ''",
            ]
        ),
        encoding="utf-8",
    )


def _build_manager(
    tmp_path: Path,
    *,
    fetcher: StubFetcher | None = None,
    fetchers: dict[str, Any] | None = None,
    secrets_path: Path | None = None,
    logger: RecordingLogger | None = None,
    cookie_checker: Any | None = None,
) -> tuple[SubscriptionManager, DummyHeartbeat, DummyQueue]:
    heartbeat = DummyHeartbeat()
    queue = DummyQueue()
    manager = SubscriptionManager(
        structured_store=StructuredStore(tmp_path / "hypo.db"),
        scheduler=DummyScheduler(),
        message_queue=queue,
        heartbeat_service=heartbeat,
        fetchers=fetchers or {"bilibili_video": fetcher or StubFetcher()},
        secrets_path=secrets_path or (tmp_path / "secrets.yaml"),
        cookie_checker=cookie_checker,
    )
    if logger is not None:
        manager_module.logger = logger
    return manager, heartbeat, queue


def test_heartbeat_no_subscriptions(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, bilibili_cookie="SESSDATA=demo")
        manager, heartbeat, queue = _build_manager(tmp_path, secrets_path=secrets_path)

        result = await manager.heartbeat_check()

        assert result is None
        assert "subscriptions" in heartbeat.sources
        assert queue.events == []

    asyncio.run(_run())


def test_cookie_expired_first_alert(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, weibo_cookie="SUB=demo")

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            assert platform == "weibo"
            assert cookie == "SUB=demo"
            return CookieHealthResult(
                platform="weibo",
                valid=False,
                error="unauthenticated",
                config_path="services.weibo.cookie",
                message="Cookie 已失效",
            )

        manager, _, queue = _build_manager(
            tmp_path,
            fetchers={"weibo": StubFetcher()},
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="weibo",
            fetcher_key="weibo",
            target_id="7360795486",
            name="草莓牛奶特别甜",
        )

        result = await manager.heartbeat_check()

        assert result is None
        assert len(queue.events) == 1
        assert "微博 Cookie 已失效" in str(queue.events[0]["summary"])
        stored = await manager.structured_store.get_preference("cookie_alert_last_weibo")
        assert stored is not None

    asyncio.run(_run())


def test_cookie_expired_within_24h(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, weibo_cookie="SUB=demo")

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            del cookie
            return CookieHealthResult(
                platform=platform,
                valid=False,
                error="unauthenticated",
                config_path="services.weibo.cookie",
                message="Cookie 已失效",
            )

        manager, _, queue = _build_manager(
            tmp_path,
            fetchers={"weibo": StubFetcher()},
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="weibo",
            fetcher_key="weibo",
            target_id="7360795486",
            name="草莓牛奶特别甜",
        )
        await manager.structured_store.set_preference(
            "cookie_alert_last_weibo",
            datetime.now(UTC).isoformat(),
        )

        result = await manager.heartbeat_check()

        assert result is None
        assert queue.events == []

    asyncio.run(_run())


def test_cookie_expired_after_24h(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, weibo_cookie="SUB=demo")

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            del cookie
            return CookieHealthResult(
                platform=platform,
                valid=False,
                error="unauthenticated",
                config_path="services.weibo.cookie",
                message="Cookie 已失效",
            )

        manager, _, queue = _build_manager(
            tmp_path,
            fetchers={"weibo": StubFetcher()},
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="weibo",
            fetcher_key="weibo",
            target_id="7360795486",
            name="草莓牛奶特别甜",
        )
        await manager.structured_store.set_preference(
            "cookie_alert_last_weibo",
            datetime(2026, 4, 10, 1, 0, tzinfo=UTC).isoformat(),
        )

        result = await manager.heartbeat_check()

        assert result is None
        assert len(queue.events) == 1
        assert "微博 Cookie 已失效" in str(queue.events[0]["summary"])

    asyncio.run(_run())


def test_heartbeat_no_updates(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, bilibili_cookie="SESSDATA=demo")
        fetcher = StubFetcher([FetchResult(ok=True, items=[])])

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            del cookie
            return CookieHealthResult(platform=platform, valid=True, username="demo")

        manager, _, queue = _build_manager(
            tmp_path,
            fetcher=fetcher,
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="bilibili",
            fetcher_key="bilibili_video",
            target_id="546195",
            name="author-demo-video",
        )

        result = await manager.heartbeat_check()

        assert result is None
        assert fetcher.calls == 1
        assert queue.events == []

    asyncio.run(_run())


def test_heartbeat_with_updates(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, bilibili_cookie="SESSDATA=demo")
        fetcher = StubFetcher(
            [
                FetchResult(
                    ok=True,
                    items=[
                        _item("1", "new-video-1"),
                        _item("2", "new-video-2"),
                    ],
                )
            ]
        )

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            del cookie
            return CookieHealthResult(platform=platform, valid=True, username="demo")

        manager, _, queue = _build_manager(
            tmp_path,
            fetcher=fetcher,
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        created = await manager.add_subscription(
            platform="bilibili",
            fetcher_key="bilibili_video",
            target_id="546195",
            name="author-demo-video",
        )

        result = await manager.heartbeat_check()

        assert result is not None
        assert "订阅更新" in result
        assert "new-video-1" in result
        assert "new-video-2" in result
        assert queue.events == []
        async with aiosqlite.connect(manager.structured_store.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM subscription_items WHERE subscription_id = ?",
                (created["id"],),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 2

    asyncio.run(_run())


def test_heartbeat_fetcher_error(tmp_path: Path) -> None:
    async def _run() -> None:
        original_logger = manager_module.logger
        logger = RecordingLogger()
        try:
            secrets_path = tmp_path / "secrets.yaml"
            _write_secrets(secrets_path, bilibili_cookie="SESSDATA=demo")
            fetcher = StubFetcher(raises=RuntimeError("boom"))

            async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
                del cookie
                return CookieHealthResult(platform=platform, valid=True, username="demo")

            manager, _, queue = _build_manager(
                tmp_path,
                fetcher=fetcher,
                secrets_path=secrets_path,
                logger=logger,
                cookie_checker=_cookie_checker,
            )
            await manager.init()
            await manager.add_subscription(
                platform="bilibili",
                fetcher_key="bilibili_video",
                target_id="546195",
                name="author-demo-video",
            )

            result = await manager.heartbeat_check()

            assert result is None
            assert queue.events == []
            assert logger.warning_calls[-1][0] == "subscription.heartbeat_error"
        finally:
            manager_module.logger = original_logger

    asyncio.run(_run())


def test_sub_status_shows_cookie_state(tmp_path: Path) -> None:
    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        _write_secrets(secrets_path, bilibili_cookie="SESSDATA=demo", weibo_cookie="SUB=demo")

        async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
            del cookie
            if platform == "bilibili":
                return CookieHealthResult(platform="bilibili", valid=True, username="demo")
            return CookieHealthResult(
                platform="weibo",
                valid=False,
                error="unauthenticated",
                config_path="services.weibo.cookie",
                message="Cookie 已失效",
            )

        manager, _, _ = _build_manager(
            tmp_path,
            fetchers={"bilibili_video": StubFetcher(), "weibo": StubFetcher()},
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="bilibili",
            fetcher_key="bilibili_video",
            target_id="546195",
            name="author-demo-video",
        )
        await manager.add_subscription(
            platform="weibo",
            fetcher_key="weibo",
            target_id="7360795486",
            name="草莓牛奶特别甜",
        )
        await manager.structured_store.set_preference(
            "cookie_alert_last_weibo",
            datetime(2026, 4, 12, 1, 0, tzinfo=UTC).isoformat(),
        )

        status = await manager.get_status()

        assert "B站订阅 (1个): ✅ Cookie 有效" in status["summary"]
        assert "微博订阅 (1个): ❌ Cookie 已失效（上次告警: 2026-04-12 09:00）" in status["summary"]

    asyncio.run(_run())
