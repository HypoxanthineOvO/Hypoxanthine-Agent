from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from hypo_agent.skills.subscription.base import NormalizedItem
from hypo_agent.skills.subscription.zhihu_pins import ZhihuPinsFetcher


def test_zhihu_pins_fetcher_parses_items_and_formats_notifications() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.zhihu.com"
        assert request.url.path == "/api/v4/members/zhang-jia-wei/pins"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "2025938258127758699",
                        "type": "pin",
                        "created": 1775801322,
                        "updated": 1775801322,
                        "excerpt_title": (
                            "\u4e2d\u56fd\u6587\u5b66\u53f2\u4e0a\u60c5\u7eea\u6700\u7a33\u5b9a\u3001"
                            "\u6700\u4e0d\u5185\u8017\u6700\u4e0d\u8ff7\u832b\u7684\u4e00\u7fa4\u4eba"
                        ),
                        "author": {
                            "name": "\u5f20\u4f73\u73ae",
                            "url_token": "zhang-jia-wei",
                        },
                        "url": "/pins/2025938258127758699",
                    }
                ]
            },
        )

    fetcher = ZhihuPinsFetcher(transport=httpx.MockTransport(_handler))

    result = asyncio.run(
        fetcher.fetch_latest(
            {
                "id": "sub-zhihu",
                "target_id": "zhang-jia-wei",
                "target_name": "\u5f20\u4f73\u73ae",
            }
        )
    )

    assert result.ok is True
    assert len(result.items) == 1
    item = result.items[0]
    assert item.item_id == "2025938258127758699"
    assert item.url == "https://www.zhihu.com/pin/2025938258127758699"
    assert item.author_name == "\u5f20\u4f73\u73ae"
    assert item.published_at == datetime.fromtimestamp(1775801322, tz=UTC)
    assert "\U0001f4a1 [\u77e5\u4e4e\u60f3\u6cd5] \u5f20\u4f73\u73ae" in fetcher.format_notification(item)


def test_zhihu_pins_fetcher_diff_and_error_classification() -> None:
    fetcher = ZhihuPinsFetcher()
    item = NormalizedItem.from_payload(
        platform="zhihu_pins",
        subscription_id="sub-zhihu",
        item_id="2025938258127758699",
        item_type="pin",
        title="title-1",
        summary="summary-1",
        url="https://www.zhihu.com/pin/2025938258127758699",
        author_id="zhang-jia-wei",
        author_name="\u5f20\u4f73\u73ae",
        published_at=datetime(2026, 4, 10, 6, 8, 42, tzinfo=UTC),
        raw_payload={"id": "2025938258127758699"},
    )
    new_item = NormalizedItem.from_payload(
        platform="zhihu_pins",
        subscription_id="sub-zhihu",
        item_id="2025519207421322683",
        item_type="pin",
        title="title-2",
        summary="summary-2",
        url="https://www.zhihu.com/pin/2025519207421322683",
        author_id="zhang-jia-wei",
        author_name="\u5f20\u4f73\u73ae",
        published_at=datetime(2026, 4, 9, 2, 23, 31, tzinfo=UTC),
        raw_payload={"id": "2025519207421322683"},
    )

    assert fetcher.diff(
        [{"platform_item_id": item.item_id, "content_hash": item.content_hash}],
        [item, new_item],
    ) == [new_item]

    request = httpx.Request("GET", "https://www.zhihu.com/api/v4/members/zhang-jia-wei/pins")
    response = httpx.Response(403, request=request)
    exc = httpx.HTTPStatusError("forbidden", request=request, response=response)
    assert fetcher.classify_error(exc) == ("anti_bot", True, False)
    assert fetcher.classify_error({"error": {"code": 10003}}) == ("anti_bot", True, False)
    assert fetcher.classify_error(RuntimeError("boom")) == ("network", True, False)
