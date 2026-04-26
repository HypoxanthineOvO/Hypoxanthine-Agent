from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import notion_client
from notion_client.errors import APIErrorCode, APIResponseError
import pytest

from hypo_agent.channels.notion.notion_client import NotionClient
from hypo_agent.channels.notion.notion_client import NotionTimeoutError, NotionUnavailableError


def test_notion_client_import_resolves_to_installed_dependency() -> None:
    assert "site-packages" in str(Path(notion_client.__file__).resolve())


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


def test_notion_client_passes_proxy_url_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    real_async_client = httpx.AsyncClient

    class RecordingAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            captured["proxy"] = kwargs.get("proxy")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.notion.notion_client.httpx.AsyncClient", RecordingAsyncClient)

    client = NotionClient("ntn_test_secret", proxy_url="http://127.0.0.1:7890")

    assert captured["proxy"] == "http://127.0.0.1:7890"
    asyncio.run(client.client.client.aclose())


def test_get_page_content_does_not_recurse_into_child_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = NotionClient("ntn_test_secret", max_retries=1)
    calls: list[str] = []

    async def fake_list(*, block_id: str, page_size: int, start_cursor: str | None = None):
        del page_size, start_cursor
        calls.append(block_id)
        if block_id == "root-page":
            return {
                "results": [
                    {
                        "id": "child-page-1",
                        "type": "child_page",
                        "has_children": True,
                        "child_page": {"title": "Nested Space"},
                    },
                    {
                        "id": "callout-1",
                        "type": "callout",
                        "has_children": True,
                        "callout": {"rich_text": []},
                    },
                ],
                "has_more": False,
            }
        if block_id == "callout-1":
            return {
                "results": [
                    {
                        "id": "paragraph-1",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": []},
                    }
                ],
                "has_more": False,
            }
        raise AssertionError(f"unexpected recursive fetch for {block_id}")

    monkeypatch.setattr(client.client.blocks.children, "list", fake_list)

    blocks = asyncio.run(client.get_page_content("root-page"))

    assert calls == ["root-page", "callout-1"]
    assert len(blocks) == 2
    assert "children" not in blocks[0]["child_page"]
    assert blocks[1]["callout"]["children"][0]["id"] == "paragraph-1"
