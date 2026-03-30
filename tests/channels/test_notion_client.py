from __future__ import annotations

import asyncio

import httpx
from notion_client.errors import APIErrorCode, APIResponseError
import pytest

from hypo_agent.channels.notion.notion_client import NotionClient
from hypo_agent.channels.notion.notion_client import NotionTimeoutError, NotionUnavailableError


def test_notion_client_preserves_auth_when_options_are_set() -> None:
    client = NotionClient("ntn_test_secret", notion_version="2022-06-28", timeout_ms=1234)

    assert client.client.options.auth == "ntn_test_secret"
    assert client.client.options.notion_version == "2022-06-28"
    assert client.client.options.timeout_ms == 1234

    request = client.client._build_request("GET", "users/me", None, None, None, None)
    assert request.headers["Authorization"] == "Bearer ntn_test_secret"
    assert request.headers["Notion-Version"] == "2022-06-28"


def test_api_error_handling(monkeypatch) -> None:
    client = NotionClient("ntn_test_secret", max_retries=1)

    async def fake_request(*args, **kwargs):
        del args, kwargs
        raise APIResponseError(
            code=APIErrorCode.ValidationError,
            status=400,
            message="body.filter.or should be defined",
            headers=httpx.Headers({}),
            raw_body_text="{}",
        )

    monkeypatch.setattr(client.client, "request", fake_request)

    with pytest.raises(NotionUnavailableError, match="ValidationError|body\\.filter\\.or should be defined"):
        asyncio.run(client.query_database("db-test", filter={"or": []}))


def test_api_timeout(monkeypatch) -> None:
    client = NotionClient("ntn_test_secret", max_retries=1, api_timeout_seconds=0.01)

    async def fake_retrieve(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(0.05)
        return {"id": "db-test"}

    monkeypatch.setattr(client.client.databases, "retrieve", fake_retrieve)

    with pytest.raises(NotionTimeoutError, match="超时"):
        asyncio.run(client.get_database("db-test"))
