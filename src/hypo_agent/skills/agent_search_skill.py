"""Tavily-backed web search skill.

Future HTTP API design notes (not implemented in this milestone):
- `POST /api/skills/agent-search/search`
  request: `{"query": "...", "max_results": 5}`
  response: `{"query": "...", "results": [...], "request_id": "..."}`
- `POST /api/skills/agent-search/read`
  request: `{"url": "https://..."}`
  response: `{"url": "...", "content": "...", "request_id": "..."}`
"""

from __future__ import annotations

import asyncio
from html import unescape
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import structlog

from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.agent_search")
_AGENT_SEARCH_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_AGENT_SEARCH_CLIENT_ERRORS = (OSError, RuntimeError, TimeoutError, httpx.HTTPError)
_HTML_BREAK_RE = re.compile(r"(?i)<br\s*/?>")
_HTML_BLOCK_CLOSE_RE = re.compile(r"(?i)</(p|div|li|h[1-6]|blockquote|pre|tr)>")
_HTML_LIST_OPEN_RE = re.compile(r"(?i)<li[^>]*>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_ZHihu_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


class AgentSearchSkill(BaseSkill):
    name = "agent_search"
    description = "Use Tavily to search the web and extract webpage content."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        secrets_path: Path | str = "config/secrets.yaml",
        tavily_client_factory: Callable[[str], Any] | None = None,
        http_transport: Any | None = None,
        max_retries: int = 1,
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self._tavily_client_factory = tavily_client_factory or self._build_default_client
        self._http_transport = http_transport
        self._max_retries = max(0, int(max_retries))
        self._client: Any | None = None
        self._client_api_key: str | None = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web and return ranked results for a query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_read",
                    "description": "Extract readable page content from a specific URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name in {"search_web", "web_search"}:
            query = str(params.get("query") or "").strip()
            if not query:
                return SkillOutput(status="error", error_info="query is required")

            max_results = int(params.get("max_results") or 5)
            max_results = min(10, max(1, max_results))
            try:
                result = await self.search_web(query, max_results=max_results)
            except _AGENT_SEARCH_ERRORS as exc:
                logger.warning("agent_search.search_web.failed", error=str(exc))
                return SkillOutput(status="error", error_info=str(exc))
            return SkillOutput(status="success", result=result)

        if tool_name == "web_read":
            url = str(params.get("url") or "").strip()
            if not url:
                return SkillOutput(status="error", error_info="url is required")

            try:
                result = await self.web_read(url)
            except _AGENT_SEARCH_ERRORS as exc:
                logger.warning("agent_search.web_read.failed", url=url, error=str(exc))
                return SkillOutput(status="error", error_info=str(exc))
            return SkillOutput(status="success", result=result)

        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def search_web(self, query: str, max_results: int = 5) -> dict[str, Any]:
        client = self._get_client()
        payload = await self._call_client_with_retry(
            client.search,
            query,
            search_depth="advanced",
            topic="general",
            max_results=max_results,
            include_answer=False,
            include_raw_content=False,
            include_favicon=True,
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        normalized_results: list[dict[str, Any]] = []
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                normalized_results.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "content": str(item.get("content") or ""),
                        "score": item.get("score"),
                        "favicon": item.get("favicon"),
                    }
                )

        return {
            "query": query,
            "results": normalized_results,
            "response_time": payload.get("response_time") if isinstance(payload, dict) else None,
            "request_id": payload.get("request_id") if isinstance(payload, dict) else None,
        }

    async def web_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        return await self.search_web(query, max_results=max_results)

    async def web_read(self, url: str) -> dict[str, Any]:
        client = self._get_client()
        fallback_error: Exception | None = None
        try:
            payload = await self._call_client_with_retry(
                client.extract,
                [url],
                extract_depth="advanced",
                format="markdown",
                include_images=True,
                include_favicon=True,
            )
            try:
                return self._normalize_extract_payload(payload, url=url)
            except ValueError as exc:
                fallback_error = exc
                if not self._is_zhihu_url(url):
                    raise
        except _AGENT_SEARCH_ERRORS as exc:
            fallback_error = exc
            if not self._is_zhihu_url(url):
                raise

        fallback = await self._read_zhihu_url(url)
        if fallback is not None:
            logger.info(
                "agent_search.web_read.zhihu_fallback",
                url=url,
                reason=str(fallback_error or "empty extract"),
            )
            return fallback
        if fallback_error is not None:
            raise ValueError(str(fallback_error))
        raise ValueError(f"No extractable content returned for URL: {url}")

    async def _call_client_with_retry(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await asyncio.to_thread(operation, *args, **kwargs)
            except _AGENT_SEARCH_CLIENT_ERRORS as exc:
                last_error = exc
                if attempt >= self._max_retries or not self._is_retryable_client_error(exc):
                    raise
                logger.info(
                    "agent_search.client_retry",
                    operation=getattr(operation, "__name__", "unknown"),
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                await asyncio.sleep(0.05 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _is_retryable_client_error(self, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.TransportError, OSError)):
            return True
        text = str(exc).strip().lower()
        return any(token in text for token in ("timeout", "timed out", "temporarily", "connection reset"))

    def _get_client(self) -> Any:
        api_key = self._load_api_key()
        if self._client is None or self._client_api_key != api_key:
            self._client = self._tavily_client_factory(api_key)
            self._client_api_key = api_key
        return self._client

    def _normalize_extract_payload(self, payload: Any, *, url: str) -> dict[str, Any]:
        results = payload.get("results") if isinstance(payload, dict) else []
        failed_results = payload.get("failed_results") if isinstance(payload, dict) else []

        if not isinstance(results, list) or not results:
            raise ValueError(f"No extractable content returned for URL: {url}")

        first = results[0]
        if not isinstance(first, dict):
            raise ValueError(f"Invalid extract response for URL: {url}")

        content = str(first.get("raw_content") or first.get("content") or "").strip()
        if not content:
            raise ValueError(f"Empty content returned for URL: {url}")

        return {
            "url": str(first.get("url") or url),
            "content": content,
            "images": first.get("images") if isinstance(first.get("images"), list) else [],
            "favicon": first.get("favicon"),
            "failed_results": failed_results if isinstance(failed_results, list) else [],
            "response_time": payload.get("response_time") if isinstance(payload, dict) else None,
            "request_id": payload.get("request_id") if isinstance(payload, dict) else None,
        }

    def _load_api_key(self) -> str:
        secrets = load_secrets_config(self.secrets_path)
        services = secrets.services
        tavily_cfg = services.tavily if services is not None else None
        api_key = (tavily_cfg.api_key if tavily_cfg is not None else "").strip()
        if not api_key:
            raise ValueError("Missing Tavily API key: config/secrets.yaml -> services.tavily.api_key")
        return api_key

    def _build_default_client(self, api_key: str) -> Any:
        try:
            from tavily import TavilyClient
        except ImportError as exc:  # pragma: no cover - depends on runtime env
            raise RuntimeError(
                "Missing dependency 'tavily-python'. Install it before using AgentSearchSkill."
            ) from exc

        return TavilyClient(api_key=api_key)

    async def _read_zhihu_url(self, url: str) -> dict[str, Any] | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None

        started_at = time.perf_counter()
        if host == "zhuanlan.zhihu.com" and len(path_parts) >= 2 and path_parts[0] == "p":
            payload = await self._zhihu_get_json(
                f"https://zhuanlan.zhihu.com/api/articles/{path_parts[1]}",
                referer=url,
            )
            content = self._render_zhihu_article(payload, source_url=url)
        elif host.endswith("zhihu.com") and path_parts[0] == "pin" and len(path_parts) >= 2:
            payload = await self._zhihu_get_json(
                f"https://www.zhihu.com/api/v4/pins/{path_parts[1]}",
                referer=url,
            )
            content = self._render_zhihu_pin(payload, source_url=url)
        elif host.endswith("zhihu.com") and path_parts[0] == "people" and len(path_parts) >= 2:
            payload = await self._zhihu_get_json(
                f"https://www.zhihu.com/api/v4/members/{path_parts[1]}",
                referer=url,
                params={"include": "headline,description,follower_count,answer_count,articles_count,pins_count"},
            )
            content = self._render_zhihu_member(payload, source_url=url)
        elif host.endswith("zhihu.com") and len(path_parts) >= 4 and path_parts[0] == "question" and path_parts[2] == "answer":
            payload = await self._zhihu_get_json(
                f"https://www.zhihu.com/api/v4/answers/{path_parts[3]}",
                referer=url,
                params={"include": "content,excerpt,comment_count,voteup_count"},
            )
            content = self._render_zhihu_answer(payload, source_url=url)
        elif host.endswith("zhihu.com") and len(path_parts) >= 2 and path_parts[0] == "question":
            payload = await self._zhihu_get_json(
                f"https://www.zhihu.com/api/v4/questions/{path_parts[1]}/answers",
                referer=url,
                params={"limit": 3, "offset": 0, "sort_by": "default", "include": "content,excerpt,comment_count,voteup_count"},
            )
            content = self._render_zhihu_question_answers(payload, source_url=url)
        else:
            return None

        resolved_url = ""
        if isinstance(payload, dict):
            resolved_url = str(payload.get("url") or "").strip()
        return {
            "url": resolved_url or url,
            "content": self._limit_text(content),
            "images": [],
            "favicon": None,
            "failed_results": [],
            "response_time": round(time.perf_counter() - started_at, 3),
            "request_id": None,
        }

    async def _zhihu_get_json(
        self,
        api_url: str,
        *,
        referer: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client_kwargs: dict[str, Any] = {
            "headers": {
                "User-Agent": _ZHihu_USER_AGENT,
                "Referer": referer,
                "Accept": "application/json, text/plain, */*",
            },
            "timeout": httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0),
            "transport": self._http_transport,
            "follow_redirects": True,
        }
        cookies = self._parse_cookie_string(self._load_zhihu_cookie())
        if cookies:
            client_kwargs["cookies"] = cookies
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid Zhihu API response for URL: {api_url}")
        return payload

    def _load_zhihu_cookie(self) -> str:
        secrets = load_secrets_config(self.secrets_path)
        services = secrets.services
        zhihu_cfg = services.zhihu if services is not None else None
        return str(zhihu_cfg.cookie if zhihu_cfg is not None else "").strip()

    def _is_zhihu_url(self, url: str) -> bool:
        host = urlparse(str(url or "")).netloc.lower()
        return host.endswith("zhihu.com")

    def _parse_cookie_string(self, raw_cookie: str) -> dict[str, str]:
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

    def _render_zhihu_pin(self, payload: dict[str, Any], *, source_url: str) -> str:
        author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        title = str(payload.get("excerpt_title") or "").strip() or f"知乎想法 {payload.get('id') or ''}".strip()
        blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
        sections = [f"# {title}", f"作者：{str(author.get('name') or '').strip() or '未知作者'}", f"来源：{source_url}", ""]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_title = str(block.get("title") or "").strip()
            block_content = self._html_to_text(block.get("content"))
            if block_title:
                sections.append(f"## {block_title}")
            if block_content:
                sections.extend([block_content, ""])
        rendered = "\n".join(sections).strip()
        if rendered:
            return rendered
        raise ValueError(f"Empty Zhihu pin content for URL: {source_url}")

    def _render_zhihu_answer(self, payload: dict[str, Any], *, source_url: str) -> str:
        author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        question = payload.get("question") if isinstance(payload.get("question"), dict) else {}
        title = str(question.get("title") or "").strip() or f"知乎回答 {payload.get('id') or ''}".strip()
        body = self._html_to_text(payload.get("content") or payload.get("excerpt"))
        if not body:
            raise ValueError(f"Empty Zhihu answer content for URL: {source_url}")
        return "\n".join(
            [
                f"# {title}",
                f"回答者：{str(author.get('name') or '').strip() or '未知作者'}",
                f"来源：{source_url}",
                "",
                body,
            ]
        ).strip()

    def _render_zhihu_question_answers(self, payload: dict[str, Any], *, source_url: str) -> str:
        answers = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not answers:
            raise ValueError(f"No Zhihu answers returned for URL: {source_url}")
        first = answers[0] if isinstance(answers[0], dict) else {}
        question = first.get("question") if isinstance(first.get("question"), dict) else {}
        title = str(question.get("title") or "").strip() or source_url
        sections = [f"# {title}", f"来源：{source_url}", ""]
        for index, answer in enumerate(answers[:3], start=1):
            if not isinstance(answer, dict):
                continue
            author = answer.get("author") if isinstance(answer.get("author"), dict) else {}
            body = self._html_to_text(answer.get("content") or answer.get("excerpt"))
            if not body:
                continue
            sections.append(f"## 回答 {index} - {str(author.get('name') or '').strip() or '未知作者'}")
            sections.extend([body, ""])
        rendered = "\n".join(sections).strip()
        if rendered:
            return rendered
        raise ValueError(f"Empty Zhihu question content for URL: {source_url}")

    def _render_zhihu_article(self, payload: dict[str, Any], *, source_url: str) -> str:
        author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        title = str(payload.get("title") or "").strip() or source_url
        body = self._html_to_text(payload.get("content") or payload.get("excerpt"))
        if not body:
            raise ValueError(f"Empty Zhihu article content for URL: {source_url}")
        return "\n".join(
            [
                f"# {title}",
                f"作者：{str(author.get('name') or '').strip() or '未知作者'}",
                f"来源：{source_url}",
                "",
                body,
            ]
        ).strip()

    def _render_zhihu_member(self, payload: dict[str, Any], *, source_url: str) -> str:
        name = str(payload.get("name") or "").strip() or "知乎用户"
        headline = str(payload.get("headline") or "").strip()
        description = self._html_to_text(payload.get("description"))
        sections = [f"# {name}", f"来源：{source_url}", ""]
        if headline:
            sections.append(f"简介：{headline}")
        counts = [
            f"关注者：{payload.get('follower_count')}" if payload.get("follower_count") is not None else "",
            f"回答：{payload.get('answer_count')}" if payload.get("answer_count") is not None else "",
            f"文章：{payload.get('articles_count')}" if payload.get("articles_count") is not None else "",
            f"想法：{payload.get('pins_count')}" if payload.get("pins_count") is not None else "",
        ]
        count_line = " | ".join(part for part in counts if part)
        if count_line:
            sections.append(count_line)
        if description:
            sections.extend(["", description])
        return "\n".join(sections).strip()

    def _html_to_text(self, value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        text = _HTML_BREAK_RE.sub("\n", text)
        text = _HTML_BLOCK_CLOSE_RE.sub("\n\n", text)
        text = _HTML_LIST_OPEN_RE.sub("- ", text)
        text = _HTML_TAG_RE.sub("", text)
        text = unescape(text)
        lines = [line.rstrip() for line in text.splitlines()]
        collapsed = "\n".join(lines)
        collapsed = _MULTI_BLANK_RE.sub("\n\n", collapsed)
        return collapsed.strip()

    def _limit_text(self, text: str, *, limit: int = 12000) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "\n\n[内容已截断]"
