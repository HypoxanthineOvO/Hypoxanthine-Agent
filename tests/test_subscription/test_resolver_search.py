from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx

from hypo_agent.skills.subscription.resolver import SearchCandidate, SubscriptionTargetResolver


def test_bilibili_search_candidates_returns_sorted_items() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                        }
                    }
                },
            )
        assert request.url.path == "/x/web-interface/wbi/search/type"
        params = parse_qs(request.url.query.decode("utf-8"))
        assert params["search_type"] == ["bili_user"]
        assert params["keyword"] == ["\u98d3\u98ce"]
        assert "w_rid" in params
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "mid": 33161,
                            "uname": "\u98d3\u98ce",
                            "usign": "",
                            "fans": 22,
                            "videos": 0,
                            "upic": "https://example.com/a.jpg",
                        },
                        {
                            "mid": 946974,
                            "uname": "\u5f71\u89c6\u98d3\u98ce",
                            "usign": "\u7528\u5f71\u50cf\u56de\u7b54\u4e16\u754c\u4e3a\u4ec0\u4e48\u8fd9\u6837",
                            "fans": 9460000,
                            "videos": 312,
                            "upic": "https://example.com/b.jpg",
                        },
                    ]
                },
            },
        )

    resolver = SubscriptionTargetResolver(
        bilibili_cookie="SESSDATA=demo",
        transport=httpx.MockTransport(_handler),
    )

    result = asyncio.run(resolver.search_candidates("bilibili", "\u98d3\u98ce", limit=5))

    assert [item.platform_id for item in result] == ["946974", "33161"]
    assert result[0] == SearchCandidate(
        platform="bilibili",
        platform_id="946974",
        name="\u5f71\u89c6\u98d3\u98ce",
        description="\u7528\u5f71\u50cf\u56de\u7b54\u4e16\u754c\u4e3a\u4ec0\u4e48\u8fd9\u6837",
        followers=9460000,
        recent_work=None,
        avatar_url="https://example.com/b.jpg",
    )


def test_weibo_search_candidates_parses_descriptions_and_followers() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ajax/side/search"
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {
                    "user": [
                        {"uid": 1, "nick": "\u8349\u8393\u725b\u5976", "description": "desc-a", "followers_count": 5},
                        {"uid": 2, "nick": "\u8349\u8393\u725b\u5976\u7279\u522b\u751c", "description": "desc-b", "followers_count": 100},
                    ]
                },
            },
        )

    resolver = SubscriptionTargetResolver(
        weibo_cookie="SUB=demo; SUBP=demo",
        transport=httpx.MockTransport(_handler),
    )

    result = asyncio.run(resolver.search_candidates("weibo", "\u8349\u8393\u725b\u5976", limit=5))

    assert [item.platform_id for item in result] == ["2", "1"]
    assert result[0].name == "\u8349\u8393\u725b\u5976\u7279\u522b\u751c"
    assert result[0].description == "desc-b"
    assert result[0].followers == 100


def test_zhihu_search_candidates_uses_api_host_and_cookie() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.zhihu.com"
        assert request.url.path == "/search_v3"
        assert request.headers["cookie"] == "z_c0=demo"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "object": {
                            "url_token": "zhang-jia-wei",
                            "name": "<em>\u5f20\u4f73\u73ae</em>",
                            "headline": "\u5199\u4f5c\u8005",
                            "follower_count": 3464885,
                            "avatar_url": "https://example.com/z.jpg",
                        }
                    },
                    {
                        "object": {
                            "url_token": "zhang-jia-wei-2",
                            "name": "<em>\u5f20\u4f73\u73ae</em>",
                            "headline": "",
                            "follower_count": 189,
                        }
                    },
                ]
            },
        )

    resolver = SubscriptionTargetResolver(
        transport=httpx.MockTransport(_handler),
    )

    result = asyncio.run(resolver.search_candidates("zhihu_pins", "\u5f20\u4f73\u73ae", cookie="z_c0=demo", limit=5))

    assert [item.platform_id for item in result] == ["zhang-jia-wei", "zhang-jia-wei-2"]
    assert result[0].name == "\u5f20\u4f73\u73ae"
    assert result[0].description == "\u5199\u4f5c\u8005"
    assert result[0].followers == 3464885


def test_search_candidates_returns_empty_list_on_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    resolver = SubscriptionTargetResolver(
        bilibili_cookie="SESSDATA=demo",
        transport=httpx.MockTransport(_handler),
    )

    result = asyncio.run(resolver.search_candidates("bilibili", "\u98d3\u98ce", limit=5))

    assert result == []
