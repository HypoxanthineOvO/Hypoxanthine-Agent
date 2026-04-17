from __future__ import annotations

import asyncio
from typing import Any

from hypo_agent.skills.subscription.resolver import ResolvedTarget, SearchCandidate
from hypo_agent.skills.subscription.skill import SubscriptionSkill


class StubResolver:
    def __init__(
        self,
        *,
        resolved: ResolvedTarget | None = None,
        candidates: list[SearchCandidate] | None = None,
    ) -> None:
        self.resolved = resolved
        self.candidates = list(candidates or [])
        self.calls: list[tuple[str, str, int]] = []

    async def resolve(self, platform: str, query: str) -> ResolvedTarget | None:
        self.calls.append(("resolve", platform, 0))
        return self.resolved

    async def search_candidates(
        self,
        platform: str,
        keyword: str,
        *,
        cookie: str | None = None,
        limit: int = 5,
    ) -> list[SearchCandidate]:
        del cookie
        self.calls.append(("search", f"{platform}:{keyword}", limit))
        return list(self.candidates[:limit])


class StubManager:
    def __init__(self, *, resolver: Any | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.target_resolver = resolver

    async def add_subscription(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("add", dict(kwargs)))
        return {"id": "sub-1", "name": kwargs["name"], "platform": kwargs["platform"], "fetcher_key": kwargs.get("fetcher_key")}

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        self.calls.append(("list", {}))
        return [{"id": "sub-1", "name": "author-demo-video", "enabled": True}]

    async def remove_subscription(self, subscription_id: str) -> bool:
        self.calls.append(("remove", {"subscription_id": subscription_id}))
        return True

    async def check_subscriptions(self, subscription_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("check", {"subscription_id": subscription_id}))
        return {"checked": 1, "new_items": 0}

    async def get_status(self) -> dict[str, Any]:
        self.calls.append(("status", {}))
        return {"total": 1, "active": 1}


def test_sub_add_with_numeric_target_id_still_creates_subscription() -> None:
    async def _run() -> None:
        manager = StubManager()
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute(
            "sub_add",
            {
                "platform": "bilibili",
                "target_id": "946974",
                "name": "\u5f71\u89c6\u98d3\u98ce",
                "interval_minutes": 10,
            },
        )

        assert result.status == "success"
        assert manager.calls == [
            (
                "add",
                {
                    "platform": "bilibili",
                    "target_id": "946974",
                    "name": "\u5f71\u89c6\u98d3\u98ce",
                    "interval_minutes": 10,
                    "fetcher_key": None,
                    "session_id": None,
                    "bootstrap": True,
                },
            )
        ]

    asyncio.run(_run())


def test_sub_add_uses_exact_resolver_match_before_search() -> None:
    async def _run() -> None:
        resolver = StubResolver(
            resolved=ResolvedTarget(
                platform="bilibili",
                query="\u5f71\u89c6\u98d3\u98ce",
                target_id="946974",
                canonical_name="\u5f71\u89c6\u98d3\u98ce",
            )
        )
        manager = StubManager(resolver=resolver)
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute(
            "sub_add",
            {
                "platform": "bilibili",
                "target_id": "\u5f71\u89c6\u98d3\u98ce",
                "name": "\u5f71\u89c6\u98d3\u98ce",
                "interval_minutes": 10,
            },
        )

        assert result.status == "success"
        assert manager.calls[0][1]["target_id"] == "946974"
        assert [call[0] for call in resolver.calls] == ["resolve"]

    asyncio.run(_run())


def test_sub_add_requires_confirmation_for_short_ambiguous_keyword() -> None:
    async def _run() -> None:
        resolver = StubResolver(
            resolved=ResolvedTarget(
                platform="bilibili",
                query="\u98d3\u98ce",
                target_id="33161",
                canonical_name="\u98d3\u98ce",
            ),
            candidates=[
                SearchCandidate(
                    platform="bilibili",
                    platform_id="3546703107983682",
                    name="\u5f71\u89c6\u98d3\u98ce\u6cd5\u52a1\u90e8",
                    description="\u5f71\u89c6\u98d3\u98ce\u6cd5\u52a1\u90e8\u5b98\u65b9\u8d26\u53f7",
                    followers=15336,
                    recent_work=None,
                    avatar_url=None,
                ),
                SearchCandidate(
                    platform="bilibili",
                    platform_id="33161",
                    name="\u98d3\u98ce",
                    description="",
                    followers=22,
                    recent_work=None,
                    avatar_url=None,
                ),
            ],
        )
        manager = StubManager(resolver=resolver)
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute(
            "sub_add",
            {
                "platform": "bilibili",
                "target_id": "\u98d3\u98ce",
                "name": "\u4e00\u6b21\u6027\u641c\u7d22\u9a8c\u6536",
                "interval_minutes": 10,
            },
        )

        assert result.status == "success"
        assert manager.calls == []
        assert "\u5f71\u89c6\u98d3\u98ce\u6cd5\u52a1\u90e8" in result.result
        assert result.metadata["requires_confirmation"] is True

    asyncio.run(_run())


def test_sub_add_returns_candidate_text_when_search_finds_matches() -> None:
    async def _run() -> None:
        resolver = StubResolver(
            candidates=[
                SearchCandidate(
                    platform="bilibili",
                    platform_id="946974",
                    name="\u5f71\u89c6\u98d3\u98ce",
                    description="\u7528\u5f71\u50cf\u56de\u7b54\u4e16\u754c\u4e3a\u4ec0\u4e48\u8fd9\u6837",
                    followers=9460000,
                    recent_work="\u4e3a\u4ec0\u4e48\u5927\u5bb6\u90fd\u5728\u7528\u8fd9\u4e2a\u955c\u5934",
                    avatar_url=None,
                ),
                SearchCandidate(
                    platform="bilibili",
                    platform_id="12345",
                    name="\u98d3\u98ce\u79d1\u6280",
                    description="\u6570\u7801\u5185\u5bb9",
                    followers=20000,
                    recent_work=None,
                    avatar_url=None,
                ),
            ]
        )
        manager = StubManager(resolver=resolver)
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute(
            "sub_add",
            {
                "platform": "bilibili",
                "target_id": "\u98d3\u98ce",
                "name": "B\u7ad9-\u98d3\u98ce",
                "interval_minutes": 10,
            },
        )

        assert result.status == "success"
        assert manager.calls == []
        assert "\u627e\u5230\u4ee5\u4e0b bilibili \u7528\u6237" in result.result
        assert "1. \u5f71\u89c6\u98d3\u98ce (946974)" in result.result
        assert "\u56de\u590d\u7f16\u53f7\u6216\u540d\u5b57\u6765\u786e\u8ba4\u8ba2\u9605" in result.result
        assert result.metadata["requires_confirmation"] is True

    asyncio.run(_run())


def test_sub_add_returns_not_found_hint_when_search_has_no_matches() -> None:
    async def _run() -> None:
        manager = StubManager(resolver=StubResolver())
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute(
            "sub_add",
            {
                "platform": "weibo",
                "target_id": "\u4e0d\u5b58\u5728\u7684\u4eba",
                "name": "\u5fae\u535a-\u4e0d\u5b58\u5728\u7684\u4eba",
                "interval_minutes": 10,
            },
        )

        assert result.status == "success"
        assert manager.calls == []
        assert "\u672a\u627e\u5230 weibo \u4e0a\u5339\u914d" in result.result

    asyncio.run(_run())


def test_sub_search_returns_candidate_text_without_creating_subscription() -> None:
    async def _run() -> None:
        manager = StubManager(
            resolver=StubResolver(
                candidates=[
                    SearchCandidate(
                        platform="zhihu_pins",
                        platform_id="zhang-jia-wei",
                        name="\u5f20\u4f73\u73ae",
                        description="\u5199\u4f5c\u8005",
                        followers=3464885,
                        recent_work=None,
                        avatar_url=None,
                    )
                ]
            )
        )
        skill = SubscriptionSkill(manager=manager)

        result = await skill.execute("sub_search", {"platform": "zhihu_pins", "keyword": "\u5f20\u4f73\u73ae"})

        assert result.status == "success"
        assert manager.calls == []
        assert "\u627e\u5230\u4ee5\u4e0b zhihu_pins \u7528\u6237" in result.result
        assert "\u5f20\u4f73\u73ae (zhang-jia-wei)" in result.result

    asyncio.run(_run())
