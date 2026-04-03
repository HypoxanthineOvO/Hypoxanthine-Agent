from __future__ import annotations

from typing import Any

import httpx

from hypo_agent.exceptions import ExternalServiceError


class InfoClientUnavailable(ExternalServiceError):
    """Raised when Hypo-Info cannot be reached or returns an unusable response."""


class InfoClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def get_homepage(self) -> dict:
        payload = await self._get_json("/api/homepage")
        return payload if isinstance(payload, dict) else {}

    async def get_articles(
        self,
        section: str | None = None,
        date: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if section:
            params["section"] = section
        if date:
            params["date"] = date
        payload = await self._get_json("/api/articles", params=params)
        return self._normalize_list_payload(payload)

    async def search_articles(self, query: str, limit: int = 10) -> list[dict]:
        payload = await self._get_json(
            "/api/articles",
            params={"q": query, "limit": limit},
        )
        return self._normalize_list_payload(payload)

    async def get_sections(self) -> list[dict]:
        payload = await self._get_json("/api/sections")
        return self._normalize_list_payload(payload)

    async def get_benchmark_ranking(self, top_n: int = 10) -> list[dict]:
        payload = await self._get_json(
            "/api/benchmark/ranking",
            params={"limit": top_n},
        )
        return self._normalize_list_payload(payload)

    async def health(self) -> dict:
        payload = await self._get_json("/api/health")
        return payload if isinstance(payload, dict) else {}

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InfoClientUnavailable(str(exc)) from exc

    @staticmethod
    def _normalize_list_payload(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "results", "data", "articles", "ranking", "sections"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []
