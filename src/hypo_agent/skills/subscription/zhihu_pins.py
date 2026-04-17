from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem

logger = structlog.get_logger("hypo_agent.skills.subscription.zhihu_pins")
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)


def _ts_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


class ZhihuPinsFetcher:
    platform = "zhihu_pins"
    fetcher_key = "zhihu_pins"

    def __init__(
        self,
        *,
        transport: Any | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout or _DEFAULT_TIMEOUT

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        user_id = str(subscription.get("target_id") or "").strip()
        if not user_id:
            return FetchResult(ok=False, items=[], error_code="schema_changed", error_message="target_id is required")
        logger.info("subscription.fetch.start", fetcher=self.fetcher_key, subscription_id=subscription.get("id"))
        try:
            async with httpx.AsyncClient(
                headers=self._build_headers(user_id),
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    f"https://www.zhihu.com/api/v4/members/{user_id}/pins",
                    params={"limit": 20, "offset": 0},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("expected JSON object")
        except Exception as exc:
            error_code, retryable, auth_stale = self.classify_error(exc)
            logger.warning(
                "subscription.fetch.error",
                fetcher=self.fetcher_key,
                subscription_id=subscription.get("id"),
                error_code=error_code,
                error=str(exc),
            )
            return FetchResult(
                ok=False,
                items=[],
                error_code=error_code,
                error_message=str(exc),
                retryable=retryable,
                auth_stale=auth_stale,
            )

        if isinstance(payload.get("error"), dict) or int(payload.get("code", 0) or 0) == 10003:
            error_code, retryable, auth_stale = self.classify_error(payload)
            return FetchResult(
                ok=False,
                items=[],
                error_code=error_code,
                error_message=str((payload.get("error") or {}).get("message") or payload.get("message") or ""),
                retryable=retryable,
                auth_stale=auth_stale,
            )

        items = self._parse_items(subscription, payload)
        logger.info(
            "subscription.fetch.done",
            fetcher=self.fetcher_key,
            subscription_id=subscription.get("id"),
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
        return f"\U0001f4a1 [\u77e5\u4e4e\u60f3\u6cd5] {item.author_name}\n{summary}\n{item.url}"

    def classify_error(self, payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]:
        if isinstance(payload, Exception):
            if isinstance(payload, httpx.HTTPStatusError):
                if payload.response.status_code == 403:
                    return ("anti_bot", True, False)
                try:
                    parsed = payload.response.json()
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    return self.classify_error(parsed)
            return ("network", True, False)
        error = payload.get("error") or {}
        code = int(error.get("code") or payload.get("code", 0) or 0)
        if code in {403, 10003}:
            return ("anti_bot", True, False)
        return ("schema_changed", False, False)

    def _parse_items(
        self,
        subscription: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[NormalizedItem]:
        pins = payload.get("data") or []
        subscription_id = str(subscription.get("id") or "")
        default_author_name = str(
            subscription.get("target_name") or subscription.get("name") or subscription.get("target_id") or ""
        )
        default_author_id = str(subscription.get("target_id") or "")
        parsed: list[NormalizedItem] = []
        for pin in pins:
            if not isinstance(pin, dict):
                continue
            pin_id = str(pin.get("id") or "").strip()
            if not pin_id:
                continue
            author = pin.get("author") or {}
            excerpt = str(pin.get("excerpt_title") or "").strip() or f"Zhihu pin {pin_id}"
            parsed.append(
                NormalizedItem.from_payload(
                    platform=self.platform,
                    subscription_id=subscription_id,
                    item_id=pin_id,
                    item_type="pin",
                    title=excerpt[:100],
                    summary=excerpt[:200],
                    url=f"https://www.zhihu.com/pin/{pin_id}",
                    author_id=str(author.get("url_token") or default_author_id),
                    author_name=str(author.get("name") or default_author_name),
                    published_at=_ts_to_datetime(pin.get("created")),
                    raw_payload=pin,
                )
            )
        return parsed

    def _build_headers(self, user_id: str) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://www.zhihu.com/people/{user_id}/pins",
            "Accept": "application/json, text/plain, */*",
        }
