from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from hypo_agent.skills.agent_search_skill import AgentSearchSkill


def _write_secrets(
    path: Path,
    *,
    api_key: str = "tvly-test-key",
    zhihu_cookie: str = "",
) -> None:
    zhihu_block = ""
    if zhihu_cookie:
        zhihu_block = f'\n  zhihu:\n    cookie: "{zhihu_cookie}"'
    path.write_text(
        f"""
providers: {{}}
services:
  tavily:
    api_key: {api_key}
{zhihu_block}
""".strip(),
        encoding="utf-8",
    )


class FakeTavilyClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.search_calls: list[dict[str, object]] = []
        self.extract_calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        return {
            "query": query,
            "results": [
                {
                    "title": "Example Result",
                    "url": "https://example.com/result",
                    "content": "Example summary",
                    "score": 0.91,
                    "favicon": "https://example.com/favicon.ico",
                }
            ],
            "response_time": 0.42,
            "request_id": "req-search-1",
        }

    def extract(self, urls, **kwargs):
        self.extract_calls.append({"urls": urls, **kwargs})
        return {
            "results": [
                {
                    "url": "https://example.com/article",
                    "raw_content": "# Example\n\nLong-form article body",
                    "images": ["https://example.com/image.png"],
                    "favicon": "https://example.com/favicon.ico",
                }
            ],
            "failed_results": [],
            "response_time": 0.73,
            "request_id": "req-read-1",
        }


def test_agent_search_skill_retries_search_timeout_once(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)

    class TimeoutThenSuccessClient(FakeTavilyClient):
        def search(self, query: str, **kwargs):
            if not self.search_calls:
                self.search_calls.append({"query": query, **kwargs})
                raise RuntimeError("Request timed out after 60 seconds.")
            return super().search(query, **kwargs)

    client = TimeoutThenSuccessClient("tvly-test-key")
    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=lambda api_key: client,
    )

    output = asyncio.run(skill.execute("search_web", {"query": "ticnote 区别", "max_results": 2}))

    assert output.status == "success"
    assert output.result["results"][0]["title"] == "Example Result"
    assert len(client.search_calls) == 2


def test_agent_search_skill_retries_web_read_timeout_once(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)

    class TimeoutThenSuccessClient(FakeTavilyClient):
        def extract(self, urls, **kwargs):
            if not self.extract_calls:
                self.extract_calls.append({"urls": urls, **kwargs})
                raise TimeoutError("extract timed out")
            return super().extract(urls, **kwargs)

    client = TimeoutThenSuccessClient("tvly-test-key")
    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=lambda api_key: client,
    )

    output = asyncio.run(skill.execute("web_read", {"url": "https://example.com/article"}))

    assert output.status == "success"
    assert "Long-form article body" in output.result["content"]
    assert len(client.extract_calls) == 2


def test_agent_search_skill_search_web_returns_normalized_results(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    captured_clients: list[FakeTavilyClient] = []

    def factory(api_key: str) -> FakeTavilyClient:
        client = FakeTavilyClient(api_key)
        captured_clients.append(client)
        return client

    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=factory,
    )

    output = asyncio.run(
        skill.execute(
            "search_web",
            {"query": "Tavily Python SDK", "max_results": 3},
        )
    )

    assert output.status == "success"
    assert output.result["query"] == "Tavily Python SDK"
    assert output.result["results"][0]["title"] == "Example Result"
    assert output.result["results"][0]["url"] == "https://example.com/result"
    assert output.result["results"][0]["content"] == "Example summary"
    assert output.result["results"][0]["score"] == 0.91
    assert output.result["response_time"] == 0.42
    assert output.result["request_id"] == "req-search-1"
    assert len(captured_clients) == 1
    assert captured_clients[0].api_key == "tvly-test-key"
    assert captured_clients[0].search_calls == [
        {
            "query": "Tavily Python SDK",
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
            "include_favicon": True,
            "max_results": 3,
            "topic": "general",
        }
    ]


def test_agent_search_skill_web_read_returns_extracted_content(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    client = FakeTavilyClient("tvly-test-key")

    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=lambda api_key: client,
    )

    output = asyncio.run(
        skill.execute(
            "web_read",
            {"url": "https://example.com/article"},
        )
    )

    assert output.status == "success"
    assert output.result == {
        "url": "https://example.com/article",
        "content": "# Example\n\nLong-form article body",
        "images": ["https://example.com/image.png"],
        "favicon": "https://example.com/favicon.ico",
        "failed_results": [],
        "response_time": 0.73,
        "request_id": "req-read-1",
    }
    assert client.extract_calls == [
        {
            "urls": ["https://example.com/article"],
            "extract_depth": "advanced",
            "format": "markdown",
            "include_images": True,
            "include_favicon": True,
        }
    ]


def test_agent_search_skill_web_read_falls_back_to_zhihu_pin_api(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path, zhihu_cookie="z_c0=demo-cookie")

    class EmptyExtractClient(FakeTavilyClient):
        def extract(self, urls, **kwargs):
            self.extract_calls.append({"urls": urls, **kwargs})
            return {
                "results": [],
                "failed_results": [{"url": urls[0], "error": "anti-bot"}],
                "response_time": 0.12,
                "request_id": "req-empty",
            }

    client = EmptyExtractClient("tvly-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/pins/2031345498871873562"
        assert request.headers["referer"].startswith("https://www.zhihu.com/pin/")
        assert request.headers["cookie"] == "z_c0=demo-cookie"
        return httpx.Response(
            200,
            json={
                "id": "2031345498871873562",
                "url": "https://www.zhihu.com/pin/2031345498871873562?native=0",
                "updated": 1777090644,
                "like_count": 12,
                "comment_count": 3,
                "author": {"name": "Hypo", "url_token": "hypo"},
                "excerpt_title": "OpenAI skills",
                "content": [
                    {
                        "title": "概览",
                        "content": "OpenAI 也开源了 skills 仓库。<br><br>现在已经能直接复用。",
                    }
                ],
            },
        )

    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=lambda api_key: client,
        http_transport=httpx.MockTransport(handler),
    )

    output = asyncio.run(
        skill.execute(
            "web_read",
            {"url": "https://www.zhihu.com/pin/2031345498871873562?utm_psn=2031530652642842068"},
        )
    )

    assert output.status == "success"
    assert output.result["url"] == "https://www.zhihu.com/pin/2031345498871873562?native=0"
    assert "OpenAI skills" in output.result["content"]
    assert "作者：Hypo" in output.result["content"]
    assert "## 概览" in output.result["content"]
    assert "现在已经能直接复用。" in output.result["content"]
    assert client.extract_calls == [
        {
            "urls": ["https://www.zhihu.com/pin/2031345498871873562?utm_psn=2031530652642842068"],
            "extract_depth": "advanced",
            "format": "markdown",
            "include_images": True,
            "include_favicon": True,
        }
    ]


def test_agent_search_skill_returns_error_when_tavily_key_missing(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("providers: {}\nservices: {}\n", encoding="utf-8")
    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=lambda api_key: FakeTavilyClient(api_key),
    )

    output = asyncio.run(skill.execute("search_web", {"query": "latest AI news"}))

    assert output.status == "error"
    assert "services.tavily.api_key" in output.error_info


def test_agent_search_skill_rejects_empty_query_without_sdk_call(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    called = False

    def factory(api_key: str) -> FakeTavilyClient:
        nonlocal called
        called = True
        return FakeTavilyClient(api_key)

    skill = AgentSearchSkill(
        secrets_path=secrets_path,
        tavily_client_factory=factory,
    )

    output = asyncio.run(skill.execute("search_web", {"query": "   "}))

    assert output.status == "error"
    assert "query is required" in output.error_info
    assert called is False
