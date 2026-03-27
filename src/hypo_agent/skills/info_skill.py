from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog

from hypo_agent.channels.info import InfoClient, InfoClientUnavailable
from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.info_skill")
_SERVICE_UNAVAILABLE = "Hypo-Info 当前不可用，请确认服务是否启动"


class InfoSkill(BaseSkill):
    name = "info"
    description = "查询 Hypo-Info 的今日资讯、文章搜索、栏目列表和模型 Benchmark 排名。"
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        secrets_path: Path | str = "config/secrets.yaml",
        info_client: Any | None = None,
        now_fn: Callable[[], datetime] | None = None,
        max_items: int = 15,
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self.now_fn = now_fn or datetime.now
        self.max_items = max(1, max_items)
        self._client = info_client or self._build_client_from_config()

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "info_today",
                    "description": "获取今日资讯摘要（可指定分区：AI/开源/Cryo/学术等）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "section": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_search",
                    "description": "在 Hypo-Info 中搜索文章",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 20,
                                "default": 10,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_benchmark",
                    "description": "获取最新的 LLM Benchmark 综合排名",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "top_n": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 20,
                                "default": 10,
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "info_sections",
                    "description": "列出 Hypo-Info 的所有内容分区",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "info_today":
                section = self._normalize_optional_text(params.get("section"))
                result = await self.info_today(section=section)
                return SkillOutput(status="success", result=result)

            if tool_name == "info_search":
                query = str(params.get("query") or "").strip()
                if not query:
                    return SkillOutput(status="error", error_info="query is required")
                limit = min(20, max(1, int(params.get("limit") or 10)))
                result = await self.info_search(query=query, limit=limit)
                return SkillOutput(status="success", result=result)

            if tool_name == "info_benchmark":
                top_n = min(20, max(1, int(params.get("top_n") or 10)))
                result = await self.info_benchmark(top_n=top_n)
                return SkillOutput(status="success", result=result)

            if tool_name == "info_sections":
                result = await self.info_sections()
                return SkillOutput(status="success", result=result)
        except (InfoClientUnavailable, httpx.HTTPError):
            return SkillOutput(status="error", error_info=_SERVICE_UNAVAILABLE)
        except Exception as exc:
            logger.warning("info_skill.execute_failed", tool_name=tool_name, error=str(exc))
            return SkillOutput(status="error", error_info=str(exc))

        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def info_today(self, *, section: str | None = None) -> str:
        today = self.now_fn().date().isoformat()
        if section:
            items = await self._client.get_articles(section=section, date=today, limit=20)
        else:
            homepage = await self._client.get_homepage()
            items = self._extract_homepage_items(homepage)
            if not items:
                items = await self._client.get_articles(date=today, limit=20)
        return self._format_today_articles(
            items,
            today=today,
            empty_message="今天暂无相关资讯",
        )

    async def info_search(self, *, query: str, limit: int = 10) -> str:
        if hasattr(self._client, "search_articles"):
            items = await self._client.search_articles(query, limit=limit)
        else:
            pool_size = max(20, limit * 10)
            items = await self._client.get_articles(limit=pool_size)

        filtered = [
            item
            for item in items
            if query.casefold() in self._searchable_text(item).casefold()
        ]
        return self._format_search_articles(
            filtered[:limit],
            empty_message=f"未找到与“{query}”相关的资讯",
        )

    async def info_benchmark(self, *, top_n: int = 10) -> str:
        items = await self._client.get_benchmark_ranking(top_n=top_n)
        if not items:
            return "暂无 Benchmark 排名数据"

        updated_at = self._extract_benchmark_updated_at(items)
        lines = [f"🏆 LLM Benchmark 排名（截至 {updated_at}）", ""]
        for index, item in enumerate(items[:top_n], start=1):
            rank = self._coerce_rank(item.get("rank"), fallback=index)
            name = str(
                item.get("model")
                or item.get("model_name")
                or item.get("name")
                or f"Model {rank}"
            ).strip()
            organization = str(
                item.get("organization")
                or item.get("org")
                or item.get("provider")
                or item.get("company")
                or ""
            ).strip()
            score = self._format_score(item.get("score") or item.get("overall_score"))
            title = f"{rank}. {name}"
            if organization:
                title += f"（{organization}）"
            lines.append(title)
            lines.append(f"    综合得分：{score}")
            strengths = self._extract_benchmark_strengths(item)
            if strengths:
                lines.append(f"    优势：{' | '.join(strengths)}")
            if index < len(items[:top_n]):
                lines.append("")
        return "\n".join(lines)

    async def info_sections(self) -> str:
        items = await self._client.get_sections()
        names: list[str] = []
        for item in items:
            name = self._extract_section_name(item)
            if name:
                names.append(name)
        return " | ".join(names) if names else "暂无内容分区"

    def _build_client_from_config(self) -> InfoClient:
        try:
            secrets = load_secrets_config(self.secrets_path)
        except FileNotFoundError as exc:
            raise ValueError(
                "Missing Hypo-Info config: config/secrets.yaml -> services.hypo_info.base_url"
            ) from exc

        services = secrets.services
        hypo_info_cfg = services.hypo_info if services is not None else None
        base_url = (hypo_info_cfg.base_url if hypo_info_cfg is not None else "").strip()
        if not base_url:
            raise ValueError(
                "Missing Hypo-Info config: config/secrets.yaml -> services.hypo_info.base_url"
            )
        return InfoClient(base_url=base_url)

    def _extract_homepage_items(self, homepage: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [
            homepage.get("today"),
            homepage.get("highlights"),
            homepage.get("headline_articles"),
            homepage.get("top"),
            homepage.get("top_articles"),
            homepage.get("articles"),
        ]
        for candidate in candidates:
            items = self._coerce_items(candidate)
            if items:
                return items

        sections = homepage.get("sections")
        if isinstance(sections, dict):
            for value in sections.values():
                items = self._coerce_items(value)
                if items:
                    return items
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                items = self._coerce_items(section.get("articles") or section.get("items"))
                if items:
                    return items
        return []

    def _format_today_articles(
        self,
        items: list[dict[str, Any]],
        *,
        today: str,
        empty_message: str,
    ) -> str:
        if not items:
            return empty_message

        total = len(items)
        shown_items = items[: self.max_items]
        blocks = [
            self._format_today_article_block(item, include_header=(index == 1), today=today, total=total)
            for index, item in enumerate(shown_items, start=1)
        ]
        if total > len(shown_items):
            blocks.append(f"共 {total} 篇，显示前 {len(shown_items)} 篇")
        return "\n\n---\n\n".join(blocks)

    def _format_search_articles(
        self,
        items: list[dict[str, Any]],
        *,
        empty_message: str,
    ) -> str:
        if not items:
            return empty_message

        blocks = [self._format_search_article_block(item) for item in items]
        return "\n\n---\n\n".join(blocks)

    def _format_today_article_block(
        self,
        item: dict[str, Any],
        *,
        include_header: bool,
        today: str,
        total: int,
    ) -> str:
        lines: list[str] = []
        if include_header:
            lines.extend([f"📅 今日资讯（共 {total} 篇）- {today}", ""])
        lines.extend(
            [
                f"📰 {self._extract_title(item)}",
                "",
                (
                    f"来源：{self._extract_source(item)} | 分区：{self._extract_section(item)} | "
                    f"重要性：{self._format_importance(item)}"
                ),
                "",
                f"摘要：{self._extract_summary(item)}",
                "",
                f"链接：{self._extract_url(item)}",
            ]
        )
        return "\n".join(lines)

    def _format_search_article_block(self, item: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"🔍 {self._extract_title(item)}",
                "",
                f"来源：{self._extract_source(item)} | 分区：{self._extract_section(item)}",
                "",
                f"摘要：{self._extract_summary(item)}",
                "",
                f"链接：{self._extract_url(item)}",
            ]
        )

    def _format_importance(self, item: dict[str, Any]) -> str:
        score_value = item.get("score") or item.get("hot") or item.get("rating")
        score = self._format_score(score_value)
        if not score:
            return "暂无评分"
        try:
            star_count = max(1, min(5, int(float(score) / 2)))
        except (TypeError, ValueError):
            return score
        return f"{'⭐' * star_count}（{score}/10）"

    def _extract_title(self, item: dict[str, Any]) -> str:
        title = str(item.get("title") or item.get("headline") or "未命名文章").strip()
        return title or "未命名文章"

    def _extract_summary(self, item: dict[str, Any]) -> str:
        summary = str(
            item.get("summary")
            or item.get("excerpt")
            or item.get("description")
            or item.get("content")
            or ""
        ).strip()
        return summary or "暂无摘要"

    def _extract_section(self, item: dict[str, Any]) -> str:
        section = item.get("section")
        if isinstance(section, dict):
            section = section.get("name") or section.get("label") or section.get("slug")
        text = str(section or item.get("category") or item.get("topic") or "未分区").strip()
        return text or "未分区"

    def _extract_url(self, item: dict[str, Any]) -> str:
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        return url or "暂无链接"

    def _extract_benchmark_updated_at(self, items: list[dict[str, Any]]) -> str:
        for item in items:
            for key in ("updated_at", "as_of", "date", "timestamp"):
                value = str(item.get(key) or "").strip()
                if value:
                    return value
        return "未知时间"

    def _extract_benchmark_strengths(self, item: dict[str, Any]) -> list[str]:
        dimensions = [
            ("coding", item.get("coding")),
            ("reasoning", item.get("reasoning")),
            ("math", item.get("math")),
            ("knowledge", item.get("knowledge")),
            ("agent", item.get("agent")),
            ("tool_use", item.get("tool_use")),
        ]
        strengths: list[str] = []
        for label, value in dimensions:
            score = self._format_score(value)
            if score:
                strengths.append(f"{label} {score}")
        return strengths

    @staticmethod
    def _extract_source(item: dict[str, Any]) -> str:
        source = item.get("source")
        if isinstance(source, dict):
            source = source.get("name") or source.get("title")
        source_text = str(source or item.get("publisher") or item.get("origin") or "未知来源").strip()
        return source_text or "未知来源"

    @staticmethod
    def _searchable_text(item: dict[str, Any]) -> str:
        fields = [
            item.get("title"),
            item.get("headline"),
            item.get("summary"),
            item.get("excerpt"),
            item.get("description"),
            item.get("content"),
        ]
        return " ".join(str(value or "") for value in fields).strip()

    @staticmethod
    def _coerce_items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("articles", "items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_section_name(item: dict[str, Any]) -> str:
        return str(item.get("name") or item.get("label") or item.get("slug") or "").strip()

    @staticmethod
    def _format_score(value: Any) -> str:
        if value is None or value == "":
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        text = f"{number:.1f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def _coerce_rank(value: Any, *, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None
