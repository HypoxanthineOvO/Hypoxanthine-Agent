from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx

from notion_client.errors import APIErrorCode, APIResponseError, HTTPResponseError, RequestTimeoutError


@dataclass(slots=True)
class ClientOptions:
    auth: str
    timeout_ms: int
    notion_version: str


async def _not_implemented(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    raise NotImplementedError("notion_client shim method was not overridden in tests")


class AsyncClient:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        payload = options or {}
        self.options = ClientOptions(
            auth=str(payload.get("auth") or ""),
            timeout_ms=int(payload.get("timeout_ms") or 60_000),
            notion_version=str(payload.get("notion_version") or "2022-06-28"),
        )
        self.pages = SimpleNamespace(
            retrieve=_not_implemented,
            update=_not_implemented,
            create=_not_implemented,
        )
        self.databases = SimpleNamespace(retrieve=_not_implemented)
        self.blocks = SimpleNamespace(
            children=SimpleNamespace(list=_not_implemented, append=_not_implemented),
            delete=_not_implemented,
        )

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        return await _not_implemented(*args, **kwargs)

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return await _not_implemented(*args, **kwargs)

    def _build_request(
        self,
        method: str,
        path: str,
        query: Any = None,
        body: Any = None,
        auth: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Request:
        del query, body, auth
        headers = {
            "Authorization": f"Bearer {self.options.auth}",
            "Notion-Version": self.options.notion_version,
        }
        if extra_headers:
            headers.update(extra_headers)
        return httpx.Request(method, f"https://api.notion.com/v1/{path}", headers=headers)


__all__ = [
    "APIErrorCode",
    "APIResponseError",
    "AsyncClient",
    "HTTPResponseError",
    "RequestTimeoutError",
]
