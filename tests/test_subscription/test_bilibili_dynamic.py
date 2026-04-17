from __future__ import annotations

import asyncio

import httpx

from hypo_agent.skills.subscription.bilibili_dynamic import BilibiliDynamicFetcher


def test_bilibili_dynamic_fetcher_parses_multiple_dynamic_types() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/x/polymer/web-dynamic/v1/feed/space"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "0",
                "data": {
                    "items": [
                        {
                            "id_str": "dyn-av",
                            "type": "DYNAMIC_TYPE_AV",
                            "modules": {
                                "module_author": {
                                    "mid": 546195,
                                    "name": "author-demo",
                                    "pub_ts": 1712700000,
                                },
                                "module_dynamic": {
                                    "desc": {"text": "video-post"},
                                    "major": {
                                        "archive": {
                                            "title": "new-video",
                                            "jump_url": "//www.bilibili.com/video/BV1xx411c7mD",
                                            "bvid": "BV1xx411c7mD",
                                        }
                                    },
                                },
                            },
                        },
                        {
                            "id_str": "dyn-word",
                            "type": "DYNAMIC_TYPE_WORD",
                            "modules": {
                                "module_author": {
                                    "mid": 546195,
                                    "name": "author-demo",
                                    "pub_ts": 1712700300,
                                },
                                "module_dynamic": {
                                    "desc": {"text": "status update today"}
                                },
                            },
                        },
                    ]
                },
            },
        )

    fetcher = BilibiliDynamicFetcher(
        cookie="SESSDATA=demo; DedeUserID=1",
        transport=httpx.MockTransport(_handler),
        wbi_keys_getter=lambda cookie, client: ("img", "sub"),
    )

    result = asyncio.run(
        fetcher.fetch_latest({"id": "sub-dyn", "target_id": "546195", "target_name": "author-demo"})
    )

    assert result.ok is True
    assert [item.item_type for item in result.items] == ["dynamic", "dynamic"]
    assert result.items[0].url == "https://www.bilibili.com/opus/dyn-av"
    assert "status update today" in result.items[1].summary
    assert "author-demo" in fetcher.format_notification(result.items[0])


def test_bilibili_dynamic_fetcher_maps_antibot_failure() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"code": -412, "message": "request was banned"})

    fetcher = BilibiliDynamicFetcher(
        cookie="SESSDATA=demo; DedeUserID=1",
        transport=httpx.MockTransport(_handler),
        wbi_keys_getter=lambda cookie, client: ("img", "sub"),
    )

    result = asyncio.run(fetcher.fetch_latest({"id": "sub-dyn", "target_id": "546195"}))

    assert result.ok is False
    assert result.error_code == "anti_bot"
    assert result.retryable is True
    assert result.auth_stale is False
