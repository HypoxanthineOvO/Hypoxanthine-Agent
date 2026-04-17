from __future__ import annotations

import asyncio

import httpx

from hypo_agent.skills.subscription.base import NormalizedItem
from hypo_agent.skills.subscription.weibo import WeiboFetcher


def test_weibo_fetcher_falls_back_to_desktop_and_parses_statuses() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "m.weibo.cn":
            return httpx.Response(
                200,
                json={
                    "ok": -100,
                    "url": "https://passport.weibo.com/sso/signin?entry=wapsso",
                },
            )
        assert request.url.host == "weibo.com"
        assert request.url.path == "/ajax/statuses/mymblog"
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {
                    "list": [
                        {
                            "id": 5267301378036174,
                            "mid": "5267301378036174",
                            "mblogid": "QseOBxIzI",
                            "created_at": "Tue Feb 17 13:26:13 +0800 2026",
                            "text_raw": "\u7b2c\u4e00\u6761\u5fae\u535a\u6b63\u6587 " * 20,
                            "reposts_count": 1,
                            "comments_count": 2,
                            "attitudes_count": 3,
                            "user": {
                                "id": 1195230310,
                                "screen_name": "\u4f55\u7085",
                            },
                        },
                        {
                            "id": 5267301378036175,
                            "mid": "5267301378036175",
                            "mblogid": "QseOBxIzJ",
                            "created_at": "Tue Feb 17 13:20:13 +0800 2026",
                            "text_raw": "\u7b2c\u4e8c\u6761\u5fae\u535a",
                            "retweeted_status": {"id": 1},
                            "user": {
                                "id": 1195230310,
                                "screen_name": "\u4f55\u7085",
                            },
                        },
                    ]
                },
            },
        )

    fetcher = WeiboFetcher(
        cookie="SUB=demo; SUBP=demo; XSRF-TOKEN=token",
        transport=httpx.MockTransport(_handler),
    )

    result = asyncio.run(
        fetcher.fetch_latest(
            {
                "id": "sub-weibo",
                "target_id": "1195230310",
                "target_name": "\u4f55\u7085",
            }
        )
    )

    assert result.ok is True
    assert [item.item_id for item in result.items] == ["5267301378036174", "5267301378036175"]
    first = result.items[0]
    second = result.items[1]
    assert first.url == "https://m.weibo.cn/detail/5267301378036174"
    assert first.author_name == "\u4f55\u7085"
    assert 0 < len(first.summary) <= 200
    assert second.item_type == "repost"
    assert "\U0001f4dd [\u5fae\u535a] \u4f55\u7085" in fetcher.format_notification(first)


def test_weibo_fetcher_diff_and_error_classification() -> None:
    fetcher = WeiboFetcher(cookie="SUB=demo")
    items = [
        NormalizedItem.from_payload(
            platform="weibo",
            subscription_id="sub-weibo",
            item_id="5267301378036174",
            item_type="status",
            title="\u7b2c\u4e00\u6761\u5fae\u535a\u6b63\u6587",
            summary="\u7b2c\u4e00\u6761\u5fae\u535a\u6b63\u6587",
            url="https://m.weibo.cn/detail/5267301378036174",
            author_id="1195230310",
            author_name="\u4f55\u7085",
            published_at=None,
            raw_payload={"id": "5267301378036174"},
        ),
        NormalizedItem.from_payload(
            platform="weibo",
            subscription_id="sub-weibo",
            item_id="5267301378036175",
            item_type="repost",
            title="\u7b2c\u4e8c\u6761\u5fae\u535a",
            summary="\u7b2c\u4e8c\u6761\u5fae\u535a",
            url="https://m.weibo.cn/detail/5267301378036175",
            author_id="1195230310",
            author_name="\u4f55\u7085",
            published_at=None,
            raw_payload={"id": "5267301378036175"},
        ),
    ]

    assert fetcher.diff(
        [{"platform_item_id": items[0].item_id, "content_hash": items[0].content_hash}],
        items,
    ) == [items[1]]

    request = httpx.Request("GET", "https://m.weibo.cn/api/container/getIndex")
    response = httpx.Response(432, request=request)
    exc = httpx.HTTPStatusError("blocked", request=request, response=response)
    assert fetcher.classify_error(exc) == ("anti_bot", True, False)
    assert fetcher.classify_error({"ok": -100, "url": "https://passport.weibo.com/login"}) == (
        "auth_stale",
        False,
        True,
    )
    assert fetcher.classify_error(RuntimeError("boom")) == ("network", True, False)
