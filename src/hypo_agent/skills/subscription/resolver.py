from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from hypo_agent.skills.subscription.wbi import get_wbi_keys, sign_params

logger = structlog.get_logger("hypo_agent.skills.subscription.resolver")
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)
_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_ZHIHU_TOKEN_RE = re.compile(r"^[0-9A-Za-z_-]+$")


def _strip_tags(value: Any) -> str:
    return _TAG_RE.sub("", str(value or "")).strip()


def _normalize_query(value: Any) -> str:
    return _NON_WORD_RE.sub("", _strip_tags(value)).lower()


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


def _normalize_platform(platform: str) -> str:
    raw = str(platform or "").strip().lower()
    if raw.startswith("bilibili"):
        return "bilibili"
    if raw in {"zhihu", "zhihu_pins"}:
        return "zhihu_pins"
    return raw


def _truncate_text(value: Any, *, limit: int = 80) -> str:
    return _strip_tags(value)[:limit]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class ResolvedTarget:
    platform: str
    query: str
    target_id: str
    canonical_name: str
    profile_url: str = ""
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class SearchCandidate:
    platform: str
    platform_id: str
    name: str
    description: str
    followers: int | None
    recent_work: str | None
    avatar_url: str | None


class SubscriptionTargetResolver:
    def __init__(
        self,
        *,
        bilibili_cookie: str = "",
        weibo_cookie: str = "",
        zhihu_cookie: str = "",
        transport: Any | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.bilibili_cookie = str(bilibili_cookie or "").strip()
        self.weibo_cookie = str(weibo_cookie or "").strip()
        self.zhihu_cookie = str(zhihu_cookie or "").strip()
        self.transport = transport
        self.timeout = timeout or _DEFAULT_TIMEOUT

    async def resolve(self, platform: str, query: str) -> ResolvedTarget | None:
        normalized_platform = _normalize_platform(platform)
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return None
        if normalized_platform == "bilibili":
            return await self._resolve_bilibili(cleaned_query)
        if normalized_platform == "weibo":
            return await self._resolve_weibo(cleaned_query)
        if normalized_platform == "zhihu_pins":
            return await self._resolve_zhihu(cleaned_query)
        return None

    async def search_candidates(
        self,
        platform: str,
        keyword: str,
        *,
        cookie: str | None = None,
        limit: int = 5,
    ) -> list[SearchCandidate]:
        normalized_platform = _normalize_platform(platform)
        cleaned_keyword = str(keyword or "").strip()
        if not cleaned_keyword:
            return []
        bounded_limit = max(1, min(int(limit or 5), 10))
        logger.info(
            "subscription.search.start",
            platform=normalized_platform,
            keyword=cleaned_keyword,
            limit=bounded_limit,
        )
        try:
            if normalized_platform == "bilibili":
                candidates = await self._search_bilibili(cleaned_keyword, limit=bounded_limit, cookie=cookie)
            elif normalized_platform == "weibo":
                candidates = await self._search_weibo(cleaned_keyword, limit=bounded_limit, cookie=cookie)
            elif normalized_platform == "zhihu_pins":
                candidates = await self._search_zhihu(cleaned_keyword, limit=bounded_limit, cookie=cookie)
            else:
                candidates = []
        except Exception as exc:
            logger.warning(
                "subscription.search.failed",
                platform=normalized_platform,
                keyword=cleaned_keyword,
                error=str(exc),
            )
            return []
        logger.info(
            "subscription.search.done",
            platform=normalized_platform,
            keyword=cleaned_keyword,
            candidate_count=len(candidates),
        )
        return candidates

    async def _resolve_bilibili(self, query: str) -> ResolvedTarget | None:
        encoded_query = quote(query, safe="")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://search.bilibili.com/upuser?keyword={encoded_query}",
            "Accept": "application/json, text/plain, */*",
        }
        if self.bilibili_cookie:
            headers["Cookie"] = self.bilibili_cookie
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={"search_type": "bili_user", "keyword": query, "page": 1},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or int(payload.get("code", 0) or 0) != 0:
            return None
        candidates = ((payload.get("data") or {}).get("result")) or []
        picked = self._pick_candidate(query, candidates, id_keys=("mid",), name_keys=("uname",), score_keys=("fans",))
        if picked is None:
            return None
        target_id = str(picked.get("mid") or "").strip()
        canonical_name = _strip_tags(picked.get("uname"))
        if not target_id or not canonical_name:
            return None
        logger.info("subscription.target.resolved", platform="bilibili", query=query, target_id=target_id)
        return ResolvedTarget(
            platform="bilibili",
            query=query,
            target_id=target_id,
            canonical_name=canonical_name,
            profile_url=f"https://space.bilibili.com/{target_id}",
            raw_payload=picked,
        )

    async def _resolve_weibo(self, query: str) -> ResolvedTarget | None:
        cookies = _parse_cookie_string(self.weibo_cookie)
        encoded_query = quote(query, safe="")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://s.weibo.com/user?q={encoded_query}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        xsrf = cookies.get("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        if self.weibo_cookie:
            headers["Cookie"] = self.weibo_cookie
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "https://weibo.com/ajax/side/search",
                params={"q": query},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or int(payload.get("ok", 0) or 0) != 1:
            return None
        data = payload.get("data") or {}
        candidates = (data.get("user") or []) + (data.get("users") or [])
        picked = self._pick_candidate(query, candidates, id_keys=("uid", "id"), name_keys=("nick",), score_keys=())
        if picked is None:
            return None
        target_id = str(picked.get("uid") or picked.get("id") or "").strip()
        canonical_name = _strip_tags(picked.get("nick"))
        if not target_id or not canonical_name:
            return None
        logger.info("subscription.target.resolved", platform="weibo", query=query, target_id=target_id)
        return ResolvedTarget(
            platform="weibo",
            query=query,
            target_id=target_id,
            canonical_name=canonical_name,
            profile_url=f"https://weibo.com/u/{target_id}",
            raw_payload=picked,
        )

    async def _resolve_zhihu(self, query: str) -> ResolvedTarget | None:
        candidates = await self.search_candidates("zhihu_pins", query, limit=10)
        if not candidates:
            return None
        picked = self._pick_search_candidate(query, candidates)
        if picked is None:
            return None
        logger.info("subscription.target.resolved", platform="zhihu_pins", query=query, target_id=picked.platform_id)
        return ResolvedTarget(
            platform="zhihu_pins",
            query=query,
            target_id=picked.platform_id,
            canonical_name=picked.name,
            profile_url=f"https://www.zhihu.com/people/{picked.platform_id}",
            raw_payload=None,
        )

    async def _search_bilibili(
        self,
        keyword: str,
        *,
        limit: int,
        cookie: str | None,
    ) -> list[SearchCandidate]:
        resolved_cookie = str(cookie or self.bilibili_cookie or "").strip()
        encoded_keyword = quote(keyword, safe="")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://search.bilibili.com/upuser?keyword={encoded_keyword}",
            "Accept": "application/json, text/plain, */*",
        }
        if resolved_cookie:
            headers["Cookie"] = resolved_cookie
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            img_key, sub_key = await get_wbi_keys(resolved_cookie, client)
            params = sign_params(
                {"keyword": keyword, "search_type": "bili_user", "page": 1, "page_size": limit},
                img_key=img_key,
                sub_key=sub_key,
            )
            response = await client.get("https://api.bilibili.com/x/web-interface/wbi/search/type", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or int(payload.get("code", 0) or 0) != 0:
            return []
        raw_results = (payload.get("data") or {}).get("result") or []
        candidates = [
            SearchCandidate(
                platform="bilibili",
                platform_id=str(item.get("mid") or "").strip(),
                name=_strip_tags(item.get("uname")),
                description=_truncate_text(item.get("usign")),
                followers=_safe_int(item.get("fans")),
                recent_work=None,
                avatar_url=str(item.get("upic") or "").strip() or None,
            )
            for item in raw_results
            if isinstance(item, dict) and str(item.get("mid") or "").strip() and _strip_tags(item.get("uname"))
        ]
        return self._finalize_candidates(candidates, limit=limit)

    async def _search_weibo(
        self,
        keyword: str,
        *,
        limit: int,
        cookie: str | None,
    ) -> list[SearchCandidate]:
        resolved_cookie = str(cookie or self.weibo_cookie or "").strip()
        cookies = _parse_cookie_string(resolved_cookie)
        encoded_keyword = quote(keyword, safe="")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://s.weibo.com/user?q={encoded_keyword}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        xsrf = cookies.get("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        if resolved_cookie:
            headers["Cookie"] = resolved_cookie
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            response = await client.get("https://weibo.com/ajax/side/search", params={"q": keyword})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or int(payload.get("ok", 0) or 0) != 1:
            return []
        data = payload.get("data") or {}
        raw_results = (data.get("user") or []) + (data.get("users") or [])
        candidates = [
            SearchCandidate(
                platform="weibo",
                platform_id=str(item.get("uid") or item.get("id") or "").strip(),
                name=_strip_tags(item.get("nick") or item.get("screen_name")),
                description=_truncate_text(item.get("description") or item.get("desc1")),
                followers=_safe_int(item.get("followers_count")),
                recent_work=None,
                avatar_url=str(item.get("avatar_large") or item.get("profile_image_url") or "").strip() or None,
            )
            for item in raw_results
            if isinstance(item, dict)
            and str(item.get("uid") or item.get("id") or "").strip()
            and _strip_tags(item.get("nick") or item.get("screen_name"))
        ]
        return self._finalize_candidates(candidates, limit=limit)

    async def _search_zhihu(
        self,
        keyword: str,
        *,
        limit: int,
        cookie: str | None,
    ) -> list[SearchCandidate]:
        resolved_cookie = str(cookie or self.zhihu_cookie or "").strip()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://www.zhihu.com/search?type=content&q={quote(keyword, safe='')}",
            "Accept": "application/json, text/plain, */*",
        }
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.timeout,
            "transport": self.transport,
            "follow_redirects": True,
        }
        if resolved_cookie:
            client_kwargs["cookies"] = _parse_cookie_string(resolved_cookie)
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(
                "https://api.zhihu.com/search_v3",
                params={"q": keyword, "t": "people", "correction": 1, "offset": 0, "limit": limit, "lc_idx": 0},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            return []
        raw_results = payload.get("data") or []
        candidates = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            obj = item.get("object") or {}
            if not isinstance(obj, dict):
                continue
            platform_id = str(obj.get("url_token") or "").strip()
            name = _strip_tags(obj.get("name"))
            if not platform_id or not name:
                continue
            candidates.append(
                SearchCandidate(
                    platform="zhihu_pins",
                    platform_id=platform_id,
                    name=name,
                    description=_truncate_text(obj.get("headline")),
                    followers=_safe_int(obj.get("follower_count")),
                    recent_work=None,
                    avatar_url=str(obj.get("avatar_url") or obj.get("avatar_url_template") or "").strip() or None,
                )
            )
        return self._finalize_candidates(candidates, limit=limit)

    def _pick_candidate(
        self,
        query: str,
        candidates: list[Any],
        *,
        id_keys: tuple[str, ...],
        name_keys: tuple[str, ...],
        score_keys: tuple[str, ...],
    ) -> dict[str, Any] | None:
        normalized_query = _normalize_query(query)
        scored: list[tuple[tuple[int, ...], dict[str, Any]]] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            candidate_id = ""
            for key in id_keys:
                candidate_id = str(candidate.get(key) or "").strip()
                if candidate_id:
                    break
            candidate_name = ""
            for key in name_keys:
                candidate_name = _strip_tags(candidate.get(key))
                if candidate_name:
                    break
            if not candidate_id or not candidate_name:
                continue
            normalized_name = _normalize_query(candidate_name)
            exact = int(normalized_name == normalized_query and normalized_query != "")
            contains = int(
                normalized_query != ""
                and normalized_name != ""
                and (normalized_query in normalized_name or normalized_name in normalized_query)
            )
            popularity = 0
            for key in score_keys:
                try:
                    popularity = max(popularity, int(candidate.get(key) or 0))
                except (TypeError, ValueError):
                    continue
            scored.append(((exact, contains, popularity, -index), candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_candidate = scored[0]
        if best_score[0] == 0 and best_score[1] == 0:
            return None
        return best_candidate

    def _pick_search_candidate(self, query: str, candidates: list[SearchCandidate]) -> SearchCandidate | None:
        normalized_query = _normalize_query(query)
        scored: list[tuple[tuple[int, int, int, int], SearchCandidate]] = []
        for index, candidate in enumerate(candidates):
            normalized_name = _normalize_query(candidate.name)
            exact = int(normalized_query != "" and normalized_name == normalized_query)
            contains = int(
                normalized_query != ""
                and normalized_name != ""
                and (normalized_query in normalized_name or normalized_name in normalized_query)
            )
            followers = int(candidate.followers or 0)
            scored.append(((exact, contains, followers, -index), candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_candidate = scored[0]
        if best_score[0] == 0 and best_score[1] == 0:
            return None
        return best_candidate

    def _finalize_candidates(self, candidates: list[SearchCandidate], *, limit: int) -> list[SearchCandidate]:
        deduped: dict[str, SearchCandidate] = {}
        for candidate in candidates:
            existing = deduped.get(candidate.platform_id)
            if existing is None or int(candidate.followers or 0) > int(existing.followers or 0):
                deduped[candidate.platform_id] = candidate
                continue
            if existing.description and not candidate.description:
                continue
            if candidate.description and not existing.description:
                deduped[candidate.platform_id] = candidate
        sorted_candidates = sorted(
            deduped.values(),
            key=lambda item: (int(item.followers or 0), item.name),
            reverse=True,
        )
        return sorted_candidates[:limit]


async def search_candidates(
    platform: str,
    keyword: str,
    cookie: str | None = None,
    limit: int = 5,
    *,
    transport: Any | None = None,
    timeout: httpx.Timeout | None = None,
) -> list[SearchCandidate]:
    normalized_platform = _normalize_platform(platform)
    kwargs: dict[str, Any] = {"transport": transport, "timeout": timeout}
    if normalized_platform == "bilibili":
        kwargs["bilibili_cookie"] = str(cookie or "")
    elif normalized_platform == "weibo":
        kwargs["weibo_cookie"] = str(cookie or "")
    elif normalized_platform == "zhihu_pins":
        kwargs["zhihu_cookie"] = str(cookie or "")
    resolver = SubscriptionTargetResolver(**kwargs)
    return await resolver.search_candidates(normalized_platform, keyword, cookie=cookie, limit=limit)


def is_direct_platform_target(platform: str, target_id: str) -> bool:
    normalized_platform = _normalize_platform(platform)
    cleaned = str(target_id or "").strip()
    if not cleaned:
        return False
    if normalized_platform in {"bilibili", "weibo"}:
        return cleaned.isdigit()
    if normalized_platform == "zhihu_pins":
        return bool(_ZHIHU_TOKEN_RE.fullmatch(cleaned))
    return True
