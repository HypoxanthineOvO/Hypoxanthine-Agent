from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from notion_client import AsyncClient
from notion_client.errors import APIErrorCode, APIResponseError, HTTPResponseError, RequestTimeoutError

from hypo_agent.exceptions import ExternalServiceError


class NotionUnavailableError(ExternalServiceError):
    """Raised when Notion API is unreachable, rate-limited beyond retry budget, or unauthorized."""


class NotionTimeoutError(NotionUnavailableError):
    """Raised when a Notion API call exceeds the per-request timeout budget."""


class NotionClient:
    def __init__(
        self,
        integration_secret: str,
        *,
        notion_version: str = "2022-06-28",
        timeout_ms: int = 30_000,
        api_timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self.integration_secret = integration_secret
        self.api_timeout_seconds = max(0.01, float(api_timeout_seconds))
        self.max_retries = max(1, int(max_retries))
        self.client = AsyncClient(
            options={
                "auth": integration_secret,
                "timeout_ms": timeout_ms,
                "notion_version": notion_version,
            },
        )

    async def get_page(self, page_id: str) -> dict[str, Any]:
        response = await self._call_with_retry(
            lambda: self.client.pages.retrieve(page_id=page_id),
            action="retrieve page",
        )
        return response if isinstance(response, dict) else {}

    async def get_database(self, database_id: str) -> dict[str, Any]:
        response = await self._call_with_retry(
            lambda: self.client.databases.retrieve(database_id=database_id),
            action="retrieve database",
        )
        return response if isinstance(response, dict) else {}

    async def get_page_content(self, page_id: str) -> list[dict[str, Any]]:
        return await self._list_block_children_recursive(page_id)

    async def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        for chunk in _chunked(blocks, 100):
            await self._call_with_retry(
                lambda chunk=chunk: self.client.blocks.children.append(
                    block_id=page_id,
                    children=chunk,
                ),
                action="append blocks",
            )

    async def delete_block(self, block_id: str) -> None:
        await self._call_with_retry(
            lambda: self.client.blocks.delete(block_id=block_id),
            action="delete block",
        )

    async def update_page_properties(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        response = await self._call_with_retry(
            lambda: self.client.pages.update(page_id=page_id, properties=properties),
            action="update page",
        )
        return response if isinstance(response, dict) else {}

    async def query_database(
        self,
        database_id: str,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start_cursor: str | None = None

        while True:
            body: dict[str, Any] = {"page_size": min(100, max(1, int(page_size)))}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if start_cursor:
                body["start_cursor"] = start_cursor
            response = await self._call_with_retry(
                lambda body=body: self.client.request(
                    path=f"databases/{database_id}/query",
                    method="POST",
                    body=body,
                ),
                action="query database",
            )
            if not isinstance(response, dict):
                break
            results = response.get("results", [])
            if isinstance(results, list):
                items.extend(item for item in results if isinstance(item, dict))
            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            start_cursor = next_cursor

        return items

    async def create_page(
        self,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        response = await self._call_with_retry(
            lambda: self.client.pages.create(
                parent=parent,
                properties=properties,
                children=children or None,
            ),
            action="create page",
        )
        return response if isinstance(response, dict) else {}

    async def search(
        self,
        query: str,
        object_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "query": query,
            "page_size": min(100, max(1, int(page_size))),
        }
        if object_type:
            kwargs["filter"] = {"property": "object", "value": object_type}
        response = await self._call_with_retry(
            lambda: self.client.search(**kwargs),
            action="search",
        )
        if not isinstance(response, dict):
            return []
        results = response.get("results", [])
        return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []

    async def _list_block_children_recursive(self, block_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start_cursor: str | None = None

        while True:
            params: dict[str, Any] = {"block_id": block_id, "page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor
            response = await self._call_with_retry(
                lambda params=params: self.client.blocks.children.list(**params),
                action="list block children",
            )
            if not isinstance(response, dict):
                break
            results = response.get("results", [])
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    if item.get("has_children") is True:
                        block_type = str(item.get("type") or "")
                        payload = item.get(block_type)
                        if isinstance(payload, dict):
                            payload["children"] = await self._list_block_children_recursive(
                                str(item.get("id") or "")
                            )
                    items.append(item)
            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            start_cursor = next_cursor

        return items

    async def _call_with_retry(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        action: str,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_with_timeout(operation, action=action)
            except NotionTimeoutError:
                raise
            except APIResponseError as exc:
                last_exc = exc
                if exc.status == 429 or exc.code == APIErrorCode.RateLimited:
                    retry_after = _retry_after_seconds(exc.headers)
                    if attempt < self.max_retries:
                        await asyncio.sleep(retry_after)
                        continue
                raise self._wrap_api_error(exc, action=action) from exc
            except RequestTimeoutError as exc:
                raise NotionTimeoutError(f"Notion {action} 超时（{self._timeout_text()}）：{exc}") from exc
            except httpx.TimeoutException as exc:
                raise NotionTimeoutError(f"Notion {action} 超时（{self._timeout_text()}）：{exc}") from exc
            except (HTTPResponseError, httpx.HTTPError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                raise NotionUnavailableError(f"Notion {action} 失败：{exc}") from exc
        raise NotionUnavailableError(f"Notion {action} 失败：{last_exc}")

    def _wrap_api_error(self, exc: APIResponseError, *, action: str) -> NotionUnavailableError:
        if exc.status in {401, 403} or exc.code == APIErrorCode.Unauthorized:
            return NotionUnavailableError(
                "Notion 认证失败或无权限，请检查 integration secret 与页面 Add Connection 授权"
            )
        if exc.code == APIErrorCode.ObjectNotFound or exc.status == 404:
            return NotionUnavailableError("Notion 资源不存在，或当前集成没有访问权限")
        if exc.status == 429 or exc.code == APIErrorCode.RateLimited:
            return NotionUnavailableError("Notion 请求过于频繁，请稍后重试")
        return NotionUnavailableError(f"Notion {action} 失败：{self._format_api_error(exc)}")

    async def _call_with_timeout(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        action: str,
    ) -> Any:
        try:
            async with asyncio.timeout(self.api_timeout_seconds):
                return await operation()
        except TimeoutError as exc:
            raise NotionTimeoutError(f"Notion {action} 超时（{self._timeout_text()}）") from exc

    def _format_api_error(self, exc: APIResponseError) -> str:
        detail = str(exc).strip() or "unknown error"
        code = self._api_error_code_text(getattr(exc, "code", None))
        if code:
            return f"{code} - {detail}"
        return detail

    def _api_error_code_text(self, code: Any) -> str:
        if code is None:
            return ""
        value = getattr(code, "value", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return str(code).strip()

    def _timeout_text(self) -> str:
        timeout_seconds = self.api_timeout_seconds
        if timeout_seconds.is_integer():
            return f"{int(timeout_seconds)}秒"
        return f"{timeout_seconds:g}秒"


def _retry_after_seconds(headers: Any) -> int:
    if headers is None:
        return 1
    try:
        value = headers.get("Retry-After")
    except AttributeError:
        return 1
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
