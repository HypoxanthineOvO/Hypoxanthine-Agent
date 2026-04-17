from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem
from hypo_agent.skills.subscription.cookie_checker import CookieHealthResult
from hypo_agent.skills.subscription.manager import SubscriptionManager
from hypo_agent.skills.subscription.resolver import ResolvedTarget


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class DummyHeartbeat:
    def __init__(self) -> None:
        self.sources: list[tuple[str, Any]] = []

    def register_event_source(self, name: str, callback: Any) -> None:
        self.sources.append((name, callback))


class DummyScheduler:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.removed: list[str] = []
        self.next_runs: dict[str, str] = {}

    def register_subscription_job(
        self,
        job_id: str,
        coro: Any,
        *,
        interval_seconds: int,
        jitter_seconds: int,
    ) -> None:
        del coro, interval_seconds, jitter_seconds
        self.registered.append(job_id)
        self.next_runs[job_id] = "2099-01-01T00:00:00+00:00"

    def remove_subscription_job(self, job_id: str) -> None:
        self.removed.append(job_id)
        self.next_runs.pop(job_id, None)

    def get_job_next_run_iso(self, job_id: str) -> str | None:
        return self.next_runs.get(job_id)


class StubFetcher:
    platform = "bilibili_video"

    def __init__(self, results: list[FetchResult] | None = None) -> None:
        self.results = list(results or [])

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        del subscription
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


class TrackingFetcher(StubFetcher):
    def __init__(self) -> None:
        super().__init__([FetchResult(ok=True, items=[])])
        self.seen_target_ids: list[str] = []

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        self.seen_target_ids.append(str(subscription.get("target_id") or ""))
        return await super().fetch_latest(subscription)


class StubResolver:
    def __init__(self, mapping: dict[tuple[str, str], ResolvedTarget]) -> None:
        self.mapping = dict(mapping)
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, platform: str, query: str) -> ResolvedTarget | None:
        key = (str(platform), str(query))
        self.calls.append(key)
        return self.mapping.get(key)


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


def _build_manager(tmp_path: Path, fetcher: StubFetcher | None = None) -> SubscriptionManager:
    queue = DummyQueue()
    heartbeat = DummyHeartbeat()
    scheduler = DummyScheduler()
    store = StructuredStore(tmp_path / "hypo.db")
    return SubscriptionManager(
        structured_store=store,
        scheduler=scheduler,
        message_queue=queue,
        heartbeat_service=heartbeat,
        fetchers={"bilibili_video": fetcher or StubFetcher()},
    )


def test_manager_diff_returns_only_new_items(tmp_path: Path) -> None:
    async def _run() -> None:
        manager = _build_manager(tmp_path)
        await manager.init()

        stored = [
            {"platform_item_id": "1"},
            {"platform_item_id": "2"},
            {"platform_item_id": "3"},
        ]
        fetched = [_item("1", "old-1"), _item("2", "old-2"), _item("3", "old-3"), _item("4", "new-4"), _item("5", "new-5")]

        new_items = manager.fetchers["bilibili_video"].diff(stored, fetched)

        assert [item.item_id for item in new_items] == ["4", "5"]

    asyncio.run(_run())


def test_manager_disables_subscription_after_three_auth_stale_failures(tmp_path: Path) -> None:
    async def _run() -> None:
        fetcher = StubFetcher(
            [
                FetchResult(ok=False, items=[], error_code="auth_stale", error_message="expired", retryable=False, auth_stale=True),
                FetchResult(ok=False, items=[], error_code="auth_stale", error_message="expired", retryable=False, auth_stale=True),
                FetchResult(ok=False, items=[], error_code="auth_stale", error_message="expired", retryable=False, auth_stale=True),
            ]
        )
        manager = _build_manager(tmp_path, fetcher=fetcher)
        await manager.init()
        created = await manager.add_subscription(
            platform="bilibili",
            fetcher_key="bilibili_video",
            target_id="546195",
            name="author-demo-video",
            interval_minutes=10,
        )

        for _ in range(3):
            await manager.poll_subscription(created["id"])

        rows = await manager.list_subscriptions()
        assert rows[0]["enabled"] is False
        assert rows[0]["consecutive_failures"] == 3
        assert manager.message_queue.events[-1]["event_type"] == "subscription_trigger"

    asyncio.run(_run())


def test_manager_classifies_common_error_codes() -> None:
    assert SubscriptionManager.classify_failure_payload({"code": -101}) == ("auth_stale", False, True)
    assert SubscriptionManager.classify_failure_payload({"code": -412}) == ("anti_bot", True, False)
    assert SubscriptionManager.classify_failure_payload(RuntimeError("boom")) == ("network", True, False)


def test_manager_builds_default_fetchers_from_supported_services(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        """
providers: {}
services:
  weibo:
    cookie: SUB=demo; SUBP=demo
  zhihu:
    cookie: z_c0=demo
""".strip(),
        encoding="utf-8",
    )
    manager = SubscriptionManager(
        structured_store=StructuredStore(tmp_path / "hypo.db"),
        scheduler=DummyScheduler(),
        message_queue=DummyQueue(),
        heartbeat_service=DummyHeartbeat(),
        secrets_path=secrets_path,
    )

    assert "weibo" in manager.fetchers
    assert "zhihu_pins" in manager.fetchers


def test_manager_sets_auth_profile_by_fetcher_type(tmp_path: Path) -> None:
    async def _run() -> None:
        manager = SubscriptionManager(
            structured_store=StructuredStore(tmp_path / "hypo.db"),
            scheduler=DummyScheduler(),
            message_queue=DummyQueue(),
            heartbeat_service=DummyHeartbeat(),
            fetchers={
                "weibo": StubFetcher(),
                "zhihu_pins": StubFetcher(),
            },
        )
        await manager.init()

        weibo_sub = await manager.add_subscription(
            platform="weibo",
            target_id="1195230310",
            name="weibo-author",
        )
        zhihu_sub = await manager.add_subscription(
            platform="zhihu_pins",
            target_id="zhang-jia-wei",
            name="zhihu-author",
        )

        assert weibo_sub["fetcher_key"] == "weibo"
        assert weibo_sub["auth_profile_id"] == "services.weibo.cookie"
        assert zhihu_sub["fetcher_key"] == "zhihu_pins"
        assert zhihu_sub["auth_profile_id"] is None

    asyncio.run(_run())


def test_manager_add_subscription_resolves_named_targets(tmp_path: Path) -> None:
    async def _run() -> None:
        resolver = StubResolver(
            {
                (
                    "bilibili",
                    "\u6211\u662f\u6d3e\u6d3e",
                ): ResolvedTarget(
                    platform="bilibili",
                    query="\u6211\u662f\u6d3e\u6d3e",
                    target_id="280156719",
                    canonical_name="-\u6211\u662f\u6d3e\u6d3e-",
                )
            }
        )
        manager = SubscriptionManager(
            structured_store=StructuredStore(tmp_path / "hypo.db"),
            scheduler=DummyScheduler(),
            message_queue=DummyQueue(),
            heartbeat_service=DummyHeartbeat(),
            fetchers={"bilibili_video": StubFetcher()},
            target_resolver=resolver,
        )
        await manager.init()

        created = await manager.add_subscription(
            platform="bilibili",
            target_id="\u6211\u662f\u6d3e\u6d3e",
            name="author-demo-video",
            fetcher_key="bilibili_video",
        )

        assert created["target_id"] == "280156719"
        assert resolver.calls == [("bilibili", "\u6211\u662f\u6d3e\u6d3e")]

    asyncio.run(_run())


def test_manager_poll_subscription_auto_repairs_named_target_ids(tmp_path: Path) -> None:
    async def _run() -> None:
        fetcher = TrackingFetcher()
        resolver = StubResolver(
            {
                (
                    "bilibili",
                    "\u4e52\u4e53Q\u5947",
                ): ResolvedTarget(
                    platform="bilibili",
                    query="\u4e52\u4e53Q\u5947",
                    target_id="357387034",
                    canonical_name="\u4e52\u4e53Q\u5947",
                )
            }
        )
        manager = SubscriptionManager(
            structured_store=StructuredStore(tmp_path / "hypo.db"),
            scheduler=DummyScheduler(),
            message_queue=DummyQueue(),
            heartbeat_service=DummyHeartbeat(),
            fetchers={"bilibili_video": fetcher},
            target_resolver=resolver,
        )
        await manager.init()
        created = await manager.add_subscription(
            platform="bilibili",
            target_id="357387034",
            name="author-demo-video",
            fetcher_key="bilibili_video",
        )

        async with aiosqlite.connect(manager.structured_store.db_path) as db:
            await db.execute(
                "UPDATE subscriptions SET target_id = ? WHERE id = ?",
                ("\u4e52\u4e53Q\u5947", created["id"]),
            )
            await db.commit()

        result = await manager.poll_subscription(created["id"])
        repaired = await manager.get_subscription(created["id"])

        assert result["status"] == "ok"
        assert fetcher.seen_target_ids == ["357387034"]
        assert repaired is not None
        assert repaired["target_id"] == "357387034"
        assert resolver.calls == [("bilibili", "\u4e52\u4e53Q\u5947")]

    asyncio.run(_run())


def test_manager_check_subscriptions_includes_cookie_health_summary(tmp_path: Path) -> None:
    async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
        if platform == "bilibili":
            assert cookie == "SESSDATA=demo"
            return CookieHealthResult(
                platform="bilibili",
                valid=False,
                error="unauthenticated",
                config_path="services.bilibili.cookie",
                message=(
                    "Cookie \u5df2\u5931\u6548\uff08\u672a\u767b\u5f55\u6001\uff0c"
                    "\u8bf7\u66f4\u65b0 secrets.yaml \u4e2d\u7684 services.bilibili.cookie\uff09"
                ),
            )
        return CookieHealthResult(
            platform="zhihu_pins",
            valid=True,
            needs_cookie=False,
            message="\u65e0\u9700 Cookie",
        )

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  bilibili:
    cookie: SESSDATA=demo
""".strip(),
            encoding="utf-8",
        )
        manager = SubscriptionManager(
            structured_store=StructuredStore(tmp_path / "hypo.db"),
            scheduler=DummyScheduler(),
            message_queue=DummyQueue(),
            heartbeat_service=DummyHeartbeat(),
            fetchers={
                "bilibili_video": StubFetcher(),
                "zhihu_pins": StubFetcher(),
            },
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="bilibili",
            target_id="546195",
            name="author-demo-video",
            fetcher_key="bilibili_video",
        )
        await manager.add_subscription(
            platform="zhihu_pins",
            target_id="zhang-jia-wei",
            name="zhihu-author",
        )

        result = await manager.check_subscriptions()

        assert result["checked"] == 2
        assert len(result["cookie_health"]) == 2
        assert result["cookie_health"][0]["platform"] == "bilibili"
        assert result["cookie_health"][0]["status"] == "invalid"
        assert result["cookie_health"][1]["status"] == "not_required"
        assert "\u26a0\ufe0f Cookie \u72b6\u6001\uff1a" in result["cookie_health_summary"]
        assert "\u274c B\u7ad9\uff1aCookie \u5df2\u5931\u6548" in result["cookie_health_summary"]
        assert "\u2139\ufe0f \u77e5\u4e4e\uff1a\u65e0\u9700 Cookie" in result["cookie_health_summary"]
        assert "\u68c0\u67e5\u5b8c\u6210\uff1a\u5171 2 \u4e2a\u8ba2\u9605\uff0c\u65b0\u589e\u5185\u5bb9 0" in result["summary"]

    asyncio.run(_run())


def test_manager_check_subscriptions_includes_recent_known_items_when_no_updates(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        first_batch = [
            NormalizedItem.from_payload(
                platform="bilibili",
                subscription_id="sub-1",
                item_id=f"item-{index}",
                item_type="video",
                title=f"\u89c6\u9891 {index}",
                summary=f"\u89c6\u9891 {index}",
                url=f"https://example.com/item-{index}",
                author_id="546195",
                author_name="author-demo",
                published_at=datetime(2026, 4, index, 8, 0, tzinfo=UTC),
                raw_payload={"id": f"item-{index}"},
            )
            for index in range(1, 7)
        ]
        fetcher = StubFetcher(
            [
                FetchResult(ok=True, items=first_batch),
                FetchResult(ok=True, items=list(first_batch)),
            ]
        )
        manager = _build_manager(tmp_path, fetcher=fetcher)
        await manager.init()
        created = await manager.add_subscription(
            platform="bilibili",
            target_id="546195",
            name="author-demo-video",
            fetcher_key="bilibili_video",
            bootstrap=True,
        )

        result = await manager.check_subscriptions(created["id"])

        assert result["checked"] == 1
        assert result["new_items"] == 0
        assert len(result["items"]) == 1
        item_result = result["items"][0]
        assert item_result["subscription_id"] == created["id"]
        assert item_result["latest_known_item"]["title"] == "\u89c6\u9891 6"
        assert item_result["latest_known_item"]["url"] == "https://example.com/item-6"
        assert item_result["latest_known_item"]["published_at"] == "2026-04-06T16:00:00+08:00"
        assert [entry["title"] for entry in item_result["recent_known_items"]] == [
            "\u89c6\u9891 6",
            "\u89c6\u9891 5",
            "\u89c6\u9891 4",
            "\u89c6\u9891 3",
            "\u89c6\u9891 2",
        ]
        assert result["latest_known_item"]["title"] == "\u89c6\u9891 6"
        assert len(result["recent_known_items"]) == 5

    asyncio.run(_run())


def test_manager_status_includes_cookie_health_without_breaking_on_checker_errors(tmp_path: Path) -> None:
    async def _cookie_checker(platform: str, cookie: str | None, **_: Any) -> CookieHealthResult:
        assert platform == "weibo"
        assert cookie == "SUB=demo"
        raise RuntimeError("checker unavailable")

    async def _run() -> None:
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text(
            """
providers: {}
services:
  weibo:
    cookie: SUB=demo
""".strip(),
            encoding="utf-8",
        )
        manager = SubscriptionManager(
            structured_store=StructuredStore(tmp_path / "hypo.db"),
            scheduler=DummyScheduler(),
            message_queue=DummyQueue(),
            heartbeat_service=DummyHeartbeat(),
            fetchers={"weibo": StubFetcher()},
            secrets_path=secrets_path,
            cookie_checker=_cookie_checker,
        )
        await manager.init()
        await manager.add_subscription(
            platform="weibo",
            target_id="1195230310",
            name="weibo-author",
        )

        status = await manager.get_status()

        assert status["total"] == 1
        assert status["active"] == 1
        assert status["cookie_health"][0]["platform"] == "weibo"
        assert status["cookie_health"][0]["status"] == "error"
        assert "checker unavailable" in (status["cookie_health"][0]["error"] or "")
        assert "Cookie \u68c0\u67e5\u5931\u8d25" in status["cookie_health_summary"]

    asyncio.run(_run())
