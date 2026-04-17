from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger("hypo_agent.skills.subscription.cookie_checker")
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)


@dataclass(slots=True)
class CookieHealthResult:
    platform: str
    valid: bool
    username: str | None = None
    error: str | None = None
    needs_cookie: bool = True
    config_path: str | None = None
    message: str | None = None


def _normalize_platform(platform: str) -> str:
    raw = str(platform or "").strip().lower()
    if raw.startswith("bilibili"):
        return "bilibili"
    if raw in {"zhihu", "zhihu_pins"}:
        return "zhihu_pins"
    return raw


def _parse_cookie_string(raw_cookie: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in str(raw_cookie or "").split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _config_path(platform: str) -> str | None:
    if platform == "bilibili":
        return "services.bilibili.cookie"
    if platform == "weibo":
        return "services.weibo.cookie"
    return None


def _missing_cookie_result(platform: str) -> CookieHealthResult:
    config_path = _config_path(platform)
    guidance = (
        f"\u672a\u914d\u7f6e Cookie\uff0c\u8bf7\u66f4\u65b0 secrets.yaml \u4e2d\u7684 {config_path}"
        if config_path
        else "\u672a\u914d\u7f6e Cookie"
    )
    return CookieHealthResult(
        platform=platform,
        valid=False,
        error="missing_cookie",
        config_path=config_path,
        message=guidance,
    )


def _invalid_cookie_result(platform: str) -> CookieHealthResult:
    config_path = _config_path(platform)
    guidance = "Cookie \u5df2\u5931\u6548\uff08\u672a\u767b\u5f55\u6001"
    if config_path:
        guidance += f"\uff0c\u8bf7\u66f4\u65b0 secrets.yaml \u4e2d\u7684 {config_path}"
    guidance += "\uff09"
    return CookieHealthResult(
        platform=platform,
        valid=False,
        error="unauthenticated",
        config_path=config_path,
        message=guidance,
    )


def _error_result(platform: str, error: str) -> CookieHealthResult:
    return CookieHealthResult(
        platform=platform,
        valid=False,
        error=error,
        config_path=_config_path(platform),
        message=f"Cookie \u68c0\u67e5\u5931\u8d25\uff08{error}\uff09",
    )


async def check_cookie_health(
    platform: str,
    cookie: str | None,
    *,
    transport: Any | None = None,
    timeout: httpx.Timeout | None = None,
) -> CookieHealthResult:
    normalized_platform = _normalize_platform(platform)
    cleaned_cookie = str(cookie or "").strip()
    if normalized_platform == "zhihu_pins":
        return CookieHealthResult(
            platform="zhihu_pins",
            valid=True,
            needs_cookie=False,
            message="\u65e0\u9700 Cookie",
        )
    if normalized_platform not in {"bilibili", "weibo"}:
        return _error_result(normalized_platform or str(platform or "").strip(), "unsupported platform")
    if not cleaned_cookie:
        return _missing_cookie_result(normalized_platform)
    try:
        if normalized_platform == "bilibili":
            return await _check_bilibili_cookie(cleaned_cookie, transport=transport, timeout=timeout)
        return await _check_weibo_cookie(cleaned_cookie, transport=transport, timeout=timeout)
    except Exception as exc:
        logger.warning(
            "subscription.cookie_health.failed",
            platform=normalized_platform,
            error=str(exc),
        )
        return _error_result(normalized_platform, str(exc))


async def _check_bilibili_cookie(
    cookie: str,
    *,
    transport: Any | None = None,
    timeout: httpx.Timeout | None = None,
) -> CookieHealthResult:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
    }
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout or _DEFAULT_TIMEOUT,
        transport=transport,
        follow_redirects=True,
    ) as client:
        response = await client.get("https://api.bilibili.com/x/web-interface/nav")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        return _error_result("bilibili", "invalid response payload")
    data = payload.get("data") or {}
    code = int(payload.get("code", 0) or 0)
    if code == 0 and bool(data.get("isLogin")):
        username = str(data.get("uname") or "").strip() or None
        message = "Cookie \u6709\u6548"
        if username:
            message += f"\uff08\u767b\u5f55\u7528\u6237\uff1a{username}\uff09"
        return CookieHealthResult(
            platform="bilibili",
            valid=True,
            username=username,
            config_path="services.bilibili.cookie",
            message=message,
        )
    if code == -101 or data.get("isLogin") is False:
        return _invalid_cookie_result("bilibili")
    message = str(payload.get("message") or payload.get("msg") or "unexpected response").strip()
    return _error_result("bilibili", message)


async def _check_weibo_cookie(
    cookie: str,
    *,
    transport: Any | None = None,
    timeout: httpx.Timeout | None = None,
) -> CookieHealthResult:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": "https://m.weibo.cn/",
        "Accept": "application/json, text/plain, */*",
    }
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout or _DEFAULT_TIMEOUT,
        transport=transport,
        cookies=_parse_cookie_string(cookie),
        follow_redirects=True,
    ) as client:
        response = await client.get("https://m.weibo.cn/api/config")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        return _error_result("weibo", "invalid response payload")
    data = payload.get("data") or {}
    ok = int(payload.get("ok", 0) or 0)
    username = str(
        data.get("screen_name")
        or data.get("nick")
        or data.get("name")
        or ((data.get("user") or {}).get("screen_name") if isinstance(data.get("user"), dict) else "")
        or ""
    ).strip() or None
    logged_in = data.get("login")
    if ok == 1 and (logged_in is True or bool(data.get("uid")) or username is not None):
        message = "Cookie \u6709\u6548"
        if username:
            message += f"\uff08\u767b\u5f55\u7528\u6237\uff1a{username}\uff09"
        return CookieHealthResult(
            platform="weibo",
            valid=True,
            username=username,
            config_path="services.weibo.cookie",
            message=message,
        )
    if ok == -100 or logged_in is False:
        return _invalid_cookie_result("weibo")
    redirect_url = str(payload.get("url") or "").lower()
    if "login" in redirect_url or "signin" in redirect_url or "passport.weibo.com" in redirect_url:
        return _invalid_cookie_result("weibo")
    message = str(payload.get("msg") or payload.get("message") or "unexpected response").strip()
    return _error_result("weibo", message)
