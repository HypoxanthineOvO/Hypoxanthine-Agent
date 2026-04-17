from __future__ import annotations

from datetime import UTC, datetime
import inspect
from typing import Any

import httpx
import structlog

from hypo_agent.skills.subscription.base import FetchResult, NormalizedItem
from hypo_agent.skills.subscription.wbi import get_wbi_keys, sign_params

logger = structlog.get_logger("hypo_agent.skills.subscription.bilibili_video")
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)


def _ts_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


class BilibiliVideoFetcher:
    platform = "bilibili"
    fetcher_key = "bilibili_video"

    def __init__(
        self,
        *,
        cookie: str,
        transport: Any | None = None,
        timeout: httpx.Timeout | None = None,
        wbi_keys_getter: Any | None = None,
    ) -> None:
        self.cookie = str(cookie or "").strip()
        self.transport = transport
        self.timeout = timeout or _DEFAULT_TIMEOUT
        self._wbi_keys_getter = wbi_keys_getter or get_wbi_keys

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult:
        uid = str(subscription.get("target_id") or "").strip()
        if not uid:
            return FetchResult(ok=False, items=[], error_code="schema_changed", error_message="target_id is required")
        headers = self._build_headers(uid)
        logger.info("subscription.fetch.start", fetcher=self.fetcher_key, subscription_id=subscription.get("id"))
        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                img_key, sub_key = await self._resolve_wbi_keys(client)
                params = sign_params({"mid": uid, "ps": 10, "pn": 1}, img_key, sub_key)
                response = await client.get(
                    "https://api.bilibili.com/x/space/wbi/arc/search",
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
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

        code = int(payload.get("code", 0) or 0)
        if code != 0:
            error_code, retryable, auth_stale = self.classify_error(payload)
            return FetchResult(
                ok=False,
                items=[],
                error_code=error_code,
                error_message=str(payload.get("message") or ""),
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
        return f"[Bilibili Video] {item.author_name}\n{item.title}\n{item.url}"

    def classify_error(self, payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]:
        if isinstance(payload, Exception):
            if isinstance(payload, httpx.HTTPStatusError):
                response_payload: dict[str, Any] | None = None
                try:
                    parsed = payload.response.json()
                    if isinstance(parsed, dict):
                        response_payload = parsed
                except ValueError:
                    response_payload = None
                if response_payload is not None:
                    return self.classify_error(response_payload)
            return ("network", True, False)
        code = int(payload.get("code", 0) or 0)
        if code in {-352, -412}:
            return ("anti_bot", True, False)
        if code == -101:
            return ("auth_stale", False, True)
        return ("schema_changed", False, False)

    def _parse_items(
        self,
        subscription: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[NormalizedItem]:
        data = payload.get("data") or {}
        items = ((data.get("list") or {}).get("vlist")) or []
        author_name = str(subscription.get("target_name") or subscription.get("name") or subscription.get("target_id") or "")
        author_id = str(subscription.get("target_id") or "")
        subscription_id = str(subscription.get("id") or "")
        parsed: list[NormalizedItem] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            aid = str(item.get("aid") or "").strip()
            if not aid:
                continue
            bvid = str(item.get("bvid") or "").strip()
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
            parsed.append(
                NormalizedItem.from_payload(
                    platform=self.platform,
                    subscription_id=subscription_id,
                    item_id=aid,
                    item_type="video",
                    title=str(item.get("title") or "").strip() or f"Bilibili video {aid}",
                    summary=str(item.get("description") or "").strip(),
                    url=url,
                    author_id=author_id,
                    author_name=author_name,
                    published_at=_ts_to_datetime(item.get("created")),
                    raw_payload=item,
                )
            )
        return parsed

    async def _resolve_wbi_keys(self, client: httpx.AsyncClient) -> tuple[str, str]:
        result = self._wbi_keys_getter(self.cookie, client)
        if inspect.isawaitable(result):
            return await result
        return result

    def _build_headers(self, uid: str) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://space.bilibili.com/{uid}/video",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": self.cookie,
        }
