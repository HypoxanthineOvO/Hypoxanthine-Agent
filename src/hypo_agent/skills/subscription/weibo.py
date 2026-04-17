from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import re
from typing import Any

import httpx
import structlog

from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem

logger = structlog.get_logger("hypo_agent.skills.subscription.weibo")
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)


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


def _html_to_text(value: Any) -> str:
    text = re.sub(r"<br\\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _weibo_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class WeiboFetcher:
    platform = "weibo"
    fetcher_key = "weibo"
    experimental = True

    def __init__(
        self,
        *,
        cookie: str,
        transport: Any | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.cookie = str(cookie or "").strip()
        self.transport = transport
        self.timeout = timeout or _DEFAULT_TIMEOUT

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        uid = str(subscription.get("target_id") or "").strip()
        if not uid:
            return FetchResult(ok=False, items=[], error_code="schema_changed", error_message="target_id is required")
        if not self.cookie:
            return FetchResult(
                ok=False,
                items=[],
                error_code="auth_stale",
                error_message="services.weibo.cookie is required",
                retryable=False,
                auth_stale=True,
            )
        logger.info("subscription.fetch.start", fetcher=self.fetcher_key, subscription_id=subscription.get("id"))
        cookies = _parse_cookie_string(self.cookie)
        errors: list[tuple[str, bool, bool, str]] = []
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
                cookies=cookies,
                follow_redirects=True,
            ) as client:
                mobile_payload = await self._request_json(
                    client,
                    "https://m.weibo.cn/api/container/getIndex",
                    params={"containerid": f"107603{uid}"},
                    headers=self._build_mobile_headers(uid, cookies),
                )
                if self._is_mobile_success(mobile_payload):
                    items = self._parse_mobile_items(subscription, mobile_payload)
                    logger.info(
                        "subscription.fetch.done",
                        fetcher=self.fetcher_key,
                        subscription_id=subscription.get("id"),
                        endpoint="mobile_api",
                        item_count=len(items),
                    )
                    return FetchResult(ok=True, items=items)
                mobile_code, mobile_retryable, mobile_auth = self.classify_error(mobile_payload)
                errors.append((mobile_code, mobile_retryable, mobile_auth, self._payload_message(mobile_payload)))
                logger.info(
                    "subscription.fetch.fallback",
                    fetcher=self.fetcher_key,
                    subscription_id=subscription.get("id"),
                    endpoint="mobile_api",
                    error_code=mobile_code,
                )

                desktop_payload = await self._request_json(
                    client,
                    "https://weibo.com/ajax/statuses/mymblog",
                    params={"uid": uid, "page": 1, "feature": 0},
                    headers=self._build_desktop_headers(uid, cookies),
                )
        except Exception as exc:
            error_code, retryable, auth_stale = self.classify_error(exc)
            logger.warning(
                "subscription.fetch.error",
                fetcher=self.fetcher_key,
                subscription_id=subscription.get("id"),
                error_code=error_code,
                error=str(exc),
            )
            errors.append((error_code, retryable, auth_stale, str(exc)))
            final_code, final_retryable, final_auth_stale, final_message = self._select_error(errors)
            return FetchResult(
                ok=False,
                items=[],
                error_code=final_code,
                error_message=final_message,
                retryable=final_retryable,
                auth_stale=final_auth_stale,
            )

        if not self._is_desktop_success(desktop_payload):
            error_code, retryable, auth_stale = self.classify_error(desktop_payload)
            logger.warning(
                "subscription.fetch.error",
                fetcher=self.fetcher_key,
                subscription_id=subscription.get("id"),
                endpoint="desktop_ajax",
                error_code=error_code,
            )
            errors.append((error_code, retryable, auth_stale, self._payload_message(desktop_payload)))
            final_code, final_retryable, final_auth_stale, final_message = self._select_error(errors)
            return FetchResult(
                ok=False,
                items=[],
                error_code=final_code,
                error_message=final_message,
                retryable=final_retryable,
                auth_stale=final_auth_stale,
            )

        items = self._parse_desktop_items(subscription, desktop_payload)
        logger.info(
            "subscription.fetch.done",
            fetcher=self.fetcher_key,
            subscription_id=subscription.get("id"),
            endpoint="desktop_ajax",
            item_count=len(items),
        )
        return FetchResult(ok=True, items=items)

    def diff(
        self,
        stored_items: list[dict[str, Any]],
        fetched_items: list[NormalizedItem],
    ) -> list[NormalizedItem]:
        seen_ids = {str(row.get("platform_item_id") or "") for row in stored_items}
        seen_hashes = {str(row.get("content_hash") or "") for row in stored_items}
        new_items = [
            item
            for item in fetched_items
            if item.item_id not in seen_ids and item.content_hash not in seen_hashes
        ]
        return sorted(new_items, key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC))

    def format_notification(self, item: NormalizedItem) -> str:
        summary = item.summary[:100]
        return f"\U0001f4dd [\u5fae\u535a] {item.author_name}\n{summary}\n{item.url}"

    def classify_error(self, payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]:
        if isinstance(payload, Exception):
            if isinstance(payload, httpx.HTTPStatusError):
                if payload.response.status_code == 432:
                    return ("anti_bot", True, False)
                try:
                    parsed = payload.response.json()
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    return self.classify_error(parsed)
            return ("network", True, False)
        ok = int(payload.get("ok", 0) or 0)
        if ok == -100:
            return ("auth_stale", False, True)
        url = str(payload.get("url") or "").lower()
        if "login" in url or "signin" in url or "passport.weibo.com" in url:
            return ("auth_stale", False, True)
        return ("schema_changed", False, False)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("expected JSON object")
        return payload

    def _is_mobile_success(self, payload: dict[str, Any]) -> bool:
        return int(payload.get("ok", 0) or 0) == 1 and bool(((payload.get("data") or {}).get("cards") or []))

    def _is_desktop_success(self, payload: dict[str, Any]) -> bool:
        return int(payload.get("ok", 0) or 0) == 1 and isinstance((payload.get("data") or {}).get("list"), list)

    def _parse_mobile_items(
        self,
        subscription: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[NormalizedItem]:
        cards = ((payload.get("data") or {}).get("cards")) or []
        parsed: list[NormalizedItem] = []
        subscription_id = str(subscription.get("id") or "")
        default_author_name = str(
            subscription.get("target_name") or subscription.get("name") or subscription.get("target_id") or ""
        )
        default_author_id = str(subscription.get("target_id") or "")
        for card in cards:
            if not isinstance(card, dict):
                continue
            mblog = card.get("mblog") or {}
            if not isinstance(mblog, dict):
                continue
            mid = str(mblog.get("mid") or mblog.get("id") or "").strip()
            if not mid:
                continue
            author = mblog.get("user") or {}
            text = _html_to_text(mblog.get("text"))
            parsed.append(
                NormalizedItem.from_payload(
                    platform=self.platform,
                    subscription_id=subscription_id,
                    item_id=mid,
                    item_type="repost" if mblog.get("retweeted_status") else "status",
                    title=(text or f"Weibo status {mid}")[:100],
                    summary=text[:200],
                    url=f"https://m.weibo.cn/detail/{mid}",
                    author_id=str(author.get("id") or default_author_id),
                    author_name=str(author.get("screen_name") or default_author_name),
                    published_at=_weibo_datetime(mblog.get("created_at")),
                    raw_payload=mblog,
                )
            )
        return parsed

    def _parse_desktop_items(
        self,
        subscription: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[NormalizedItem]:
        statuses = ((payload.get("data") or {}).get("list")) or []
        parsed: list[NormalizedItem] = []
        subscription_id = str(subscription.get("id") or "")
        default_author_name = str(
            subscription.get("target_name") or subscription.get("name") or subscription.get("target_id") or ""
        )
        default_author_id = str(subscription.get("target_id") or "")
        for status in statuses:
            if not isinstance(status, dict):
                continue
            mid = str(status.get("mid") or status.get("id") or "").strip()
            if not mid:
                continue
            author = status.get("user") or {}
            text = _html_to_text(status.get("text_raw") or status.get("text"))
            parsed.append(
                NormalizedItem.from_payload(
                    platform=self.platform,
                    subscription_id=subscription_id,
                    item_id=mid,
                    item_type="repost" if status.get("retweeted_status") else "status",
                    title=(text or f"Weibo status {mid}")[:100],
                    summary=text[:200],
                    url=f"https://m.weibo.cn/detail/{mid}",
                    author_id=str(author.get("id") or default_author_id),
                    author_name=str(author.get("screen_name") or default_author_name),
                    published_at=_weibo_datetime(status.get("created_at")),
                    raw_payload=status,
                )
            )
        return parsed

    def _build_mobile_headers(self, uid: str, cookies: dict[str, str]) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1"
            ),
            "Referer": f"https://m.weibo.cn/u/{uid}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        xsrf = cookies.get("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        return headers

    def _build_desktop_headers(self, uid: str, cookies: dict[str, str]) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://weibo.com/u/{uid}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        xsrf = cookies.get("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        return headers

    def _payload_message(self, payload: dict[str, Any]) -> str:
        for key in ("msg", "message", "url"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return "unexpected weibo payload"

    def _select_error(
        self,
        errors: list[tuple[str, bool, bool, str]],
    ) -> tuple[str, bool, bool, str]:
        if not errors:
            return ("network", True, False, "unknown weibo error")
        priority = {
            "auth_stale": 0,
            "anti_bot": 1,
            "network": 2,
            "schema_changed": 3,
        }
        best = min(errors, key=lambda item: priority.get(item[0], 99))
        return best
