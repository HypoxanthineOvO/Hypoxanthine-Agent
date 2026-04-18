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
from pathlib import Path
from typing import Any, Callable

import structlog

from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.agent_search")
_AGENT_SEARCH_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class AgentSearchSkill(BaseSkill):
    name = "agent_search"
    description = "Use Tavily to search the web and extract webpage content."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        secrets_path: Path | str = "config/secrets.yaml",
        tavily_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self._tavily_client_factory = tavily_client_factory or self._build_default_client
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
        payload = await asyncio.to_thread(
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
        payload = await asyncio.to_thread(
            client.extract,
            [url],
            extract_depth="advanced",
            format="markdown",
            include_images=True,
            include_favicon=True,
        )
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

    def _get_client(self) -> Any:
        api_key = self._load_api_key()
        if self._client is None or self._client_api_key != api_key:
            self._client = self._tavily_client_factory(api_key)
            self._client_api_key = api_key
        return self._client

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
