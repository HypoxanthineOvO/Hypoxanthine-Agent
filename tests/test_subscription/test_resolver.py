from __future__ import annotations

import asyncio

import httpx

from hypo_agent.skills.subscription.resolver import SubscriptionTargetResolver


def test_bilibili_target_resolver_matches_normalized_name() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/x/web-interface/search/type"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "mid": 280156719,
                            "uname": "-\u6211\u662f\u6d3e\u6d3e-",
                            "fans": 12345,
                        },
                        {
                            "mid": 1273501826,
                            "uname": "\u6211\u662f\u6d3e\u6d3e\u600e\u4e48\u6709\u91cd\u540d",
                            "fans": 100,
                        },
                    ]
                },
            },
        )

    resolver = SubscriptionTargetResolver(
        bilibili_cookie="SESSDATA=demo",
        transport=httpx.MockTransport(_handler),
    )

    resolved = asyncio.run(resolver.resolve("bilibili", "\u6211\u662f\u6d3e\u6d3e"))

    assert resolved is not None
    assert resolved.target_id == "280156719"
    assert resolved.canonical_name == "-\u6211\u662f\u6d3e\u6d3e-"


def test_weibo_target_resolver_matches_user_uid() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ajax/side/search"
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {
                    "user": [
                        {
                            "uid": 7360795486,
                            "nick": "\u8349\u8393\u725b\u5976\u7279\u522b\u751c",
                        }
                    ]
                },
            },
        )

    resolver = SubscriptionTargetResolver(
        weibo_cookie="SUB=demo; SUBP=demo",
        transport=httpx.MockTransport(_handler),
    )

    resolved = asyncio.run(resolver.resolve("weibo", "\u8349\u8393\u725b\u5976\u7279\u522b\u751c"))

    assert resolved is not None
    assert resolved.target_id == "7360795486"
    assert resolved.canonical_name == "\u8349\u8393\u725b\u5976\u7279\u522b\u751c"
