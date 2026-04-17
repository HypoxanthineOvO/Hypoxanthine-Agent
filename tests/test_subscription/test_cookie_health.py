from __future__ import annotations

import asyncio

import httpx

from hypo_agent.skills.subscription.cookie_checker import check_cookie_health


def test_bilibili_cookie_health_reports_logged_in_user() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bilibili.com"
        assert request.url.path == "/x/web-interface/nav"
        assert request.headers["Cookie"] == "SESSDATA=demo"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "isLogin": True,
                    "uname": "Hypoxanthine",
                },
            },
        )

    result = asyncio.run(
        check_cookie_health(
            "bilibili",
            "SESSDATA=demo",
            transport=httpx.MockTransport(_handler),
        )
    )

    assert result.platform == "bilibili"
    assert result.valid is True
    assert result.username == "Hypoxanthine"
    assert result.error is None
    assert result.message == "Cookie \u6709\u6548\uff08\u767b\u5f55\u7528\u6237\uff1aHypoxanthine\uff09"


def test_bilibili_cookie_health_reports_invalid_login() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bilibili.com"
        return httpx.Response(
            200,
            json={
                "code": -101,
                "message": "\u8d26\u53f7\u672a\u767b\u5f55",
                "data": {
                    "isLogin": False,
                },
            },
        )

    result = asyncio.run(
        check_cookie_health(
            "bilibili",
            "SESSDATA=expired",
            transport=httpx.MockTransport(_handler),
        )
    )

    assert result.valid is False
    assert result.error == "unauthenticated"
    assert "services.bilibili.cookie" in (result.message or "")


def test_weibo_cookie_health_reports_valid_cookie() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "m.weibo.cn"
        assert request.url.path == "/api/config"
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {
                    "login": True,
                    "uid": "1195230310",
                    "screen_name": "Hypoxanthine",
                },
            },
        )

    result = asyncio.run(
        check_cookie_health(
            "weibo",
            "SUB=demo; SUBP=demo",
            transport=httpx.MockTransport(_handler),
        )
    )

    assert result.platform == "weibo"
    assert result.valid is True
    assert result.username == "Hypoxanthine"
    assert result.error is None


def test_weibo_cookie_health_reports_invalid_cookie() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "m.weibo.cn"
        return httpx.Response(
            200,
            json={
                "ok": -100,
                "msg": "login required",
                "data": {
                    "login": False,
                },
            },
        )

    result = asyncio.run(
        check_cookie_health(
            "weibo",
            "SUB=expired",
            transport=httpx.MockTransport(_handler),
        )
    )

    assert result.valid is False
    assert result.error == "unauthenticated"
    assert "services.weibo.cookie" in (result.message or "")


def test_zhihu_cookie_health_reports_not_required() -> None:
    result = asyncio.run(check_cookie_health("zhihu_pins", None))

    assert result.platform == "zhihu_pins"
    assert result.valid is True
    assert result.needs_cookie is False
    assert result.message == "\u65e0\u9700 Cookie"


def test_cookie_health_handles_network_errors_gracefully() -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom", request=request)))

    result = asyncio.run(check_cookie_health("weibo", "SUB=demo", transport=transport))

    assert result.valid is False
    assert result.error is not None
    assert "boom" in result.error
    assert "\u68c0\u67e5\u5931\u8d25" in (result.message or "")
