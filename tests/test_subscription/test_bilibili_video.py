from __future__ import annotations

import asyncio
from typing import Any

import httpx

from hypo_agent.skills.subscription.bilibili_video import BilibiliVideoFetcher


def test_bilibili_video_fetcher_parses_vlist_items() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/x/space/wbi/arc/search"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "OK",
                "data": {
                    "list": {
                        "vlist": [
                            {
                                "aid": 123,
                                "bvid": "BV1xx411c7mD",
                                "title": "new-video",
                                "created": 1712700000,
                                "description": "video-description",
                                "pic": "https://i0.hdslb.com/bfs/archive/demo.jpg",
                            }
                        ]
                    }
                },
            },
        )

    fetcher = BilibiliVideoFetcher(
        cookie="SESSDATA=demo; DedeUserID=1",
        transport=httpx.MockTransport(_handler),
        wbi_keys_getter=lambda cookie, client: ("img", "sub"),
    )

    result = asyncio.run(
        fetcher.fetch_latest(
            {"id": "sub-video", "target_id": "546195", "target_name": "author-demo"}
        )
    )

    assert result.ok is True
    assert len(result.items) == 1
    item = result.items[0]
    assert item.item_id == "123"
    assert item.title == "new-video"
    assert item.url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert "author-demo" in fetcher.format_notification(item)


def test_bilibili_video_fetcher_maps_auth_failure() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -101, "message": "not logged in"})

    fetcher = BilibiliVideoFetcher(
        cookie="SESSDATA=demo; DedeUserID=1",
        transport=httpx.MockTransport(_handler),
        wbi_keys_getter=lambda cookie, client: ("img", "sub"),
    )

    result = asyncio.run(fetcher.fetch_latest({"id": "sub-video", "target_id": "546195"}))

    assert result.ok is False
    assert result.error_code == "auth_stale"
    assert result.auth_stale is True
    assert result.retryable is False
