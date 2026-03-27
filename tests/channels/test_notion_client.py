from __future__ import annotations

from hypo_agent.channels.notion.notion_client import NotionClient


def test_notion_client_preserves_auth_when_options_are_set() -> None:
    client = NotionClient("ntn_test_secret", notion_version="2022-06-28", timeout_ms=1234)

    assert client.client.options.auth == "ntn_test_secret"
    assert client.client.options.notion_version == "2022-06-28"
    assert client.client.options.timeout_ms == 1234

    request = client.client._build_request("GET", "users/me", None, None, None, None)
    assert request.headers["Authorization"] == "Bearer ntn_test_secret"
    assert request.headers["Notion-Version"] == "2022-06-28"
