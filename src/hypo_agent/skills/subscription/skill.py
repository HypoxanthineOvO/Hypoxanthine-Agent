from __future__ import annotations

import re
from typing import Any

import structlog

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill
from hypo_agent.skills.subscription.manager import SubscriptionManager
from hypo_agent.skills.subscription.resolver import ResolvedTarget, SearchCandidate, is_direct_platform_target

logger = structlog.get_logger("hypo_agent.skills.subscription.skill")
_QUERY_TOKEN_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")
_ALL_SUBSCRIPTIONS_ALIASES = {
    "",
    "*",
    "all",
    "enabled",
    "all_enabled",
    "subscriptions",
    "\u5168\u90e8",
    "\u6240\u6709",
    "\u5168\u90e8\u8ba2\u9605",
    "\u6240\u6709\u8ba2\u9605",
    "\u5df2\u542f\u7528",
    "\u5df2\u542f\u7528\u8ba2\u9605",
}


def _format_followers(value: int | None) -> str:
    if value is None:
        return "\u7c89\u4e1d\u6570\u672a\u77e5"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}\u4ebf\u7c89\u4e1d"
    if value >= 10_000:
        return f"{value / 10_000:.1f}\u4e07\u7c89\u4e1d"
    return f"{value}\u7c89\u4e1d"


def _format_candidates(platform: str, candidates: list[SearchCandidate]) -> str:
    lines = [f"\u627e\u5230\u4ee5\u4e0b {platform} \u7528\u6237\uff0c\u8bf7\u786e\u8ba4\u8981\u8ba2\u9605\u54ea\u4e2a\uff1a"]
    for index, candidate in enumerate(candidates, start=1):
        line = f"{index}. {candidate.name} ({candidate.platform_id}) -- {_format_followers(candidate.followers)}"
        if candidate.recent_work:
            line += f" -- \u6700\u8fd1\uff1a\u300c{candidate.recent_work[:60]}\u300d"
        lines.append(line)
        if candidate.description:
            lines.append(f"   {candidate.description[:60]}")
    lines.append("")
    lines.append('\u56de\u590d\u7f16\u53f7\u6216\u540d\u5b57\u6765\u786e\u8ba4\u8ba2\u9605\uff0c\u6216\u56de\u590d"\u53d6\u6d88"\u3002')
    return "\n".join(lines)


def _query_token_length(value: str) -> int:
    return len("".join(_QUERY_TOKEN_RE.findall(str(value or ""))))


def _normalize_subscription_check_target(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    if cleaned.lower() in _ALL_SUBSCRIPTIONS_ALIASES or cleaned in _ALL_SUBSCRIPTIONS_ALIASES:
        return None
    return cleaned or None


class SubscriptionSkill(BaseSkill):
    name = "subscription"
    description = (
        "\u7ba1\u7406 B \u7ad9\u3001\u5fae\u535a\u3001"
        "\u77e5\u4e4e\u7b49\u5e73\u53f0\u7684\u8ba2\u9605\u67e5\u770b\u3001"
        "\u641c\u7d22\u4e0e\u63a8\u9001\u3002"
    )
    keyword_hints = [
        "B\u7ad9\u8ba2\u9605",
        "\u54d4\u54e9\u54d4\u54e9\u8ba2\u9605",
        "\u5fae\u535a\u8ba2\u9605",
        "\u77e5\u4e4e\u8ba2\u9605",
        "\u5173\u6ce8\u7684UP\u4e3b",
        "UP\u4e3b",
        "\u8ba2\u9605",
    ]
    required_permissions: list[str] = []

    def __init__(self, *, manager: SubscriptionManager) -> None:
        self.manager = manager

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "sub_add",
                    "description": "Create a content subscription and run an immediate bootstrap fetch.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {
                                "type": "string",
                                "description": "Platform name, e.g. bilibili.",
                            },
                            "target_id": {
                                "type": "string",
                                "description": "Platform user ID, url_token, exact name, or search keyword.",
                            },
                            "name": {
                                "type": "string",
                                "description": "Human-readable subscription name. Optional when target is obvious.",
                            },
                            "interval_minutes": {"type": "integer", "minimum": 1},
                            "fetcher_key": {
                                "type": "string",
                                "description": "Optional fetcher override, e.g. bilibili_video or bilibili_dynamic.",
                            },
                        },
                        "required": ["platform", "target_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sub_search",
                    "description": "Search candidate accounts on a platform without creating a subscription.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "keyword": {"type": "string"},
                        },
                        "required": ["platform", "keyword"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sub_list",
                    "description": "List all subscriptions with status and failure counters.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sub_remove",
                    "description": "Delete a subscription and unschedule its polling job.",
                    "parameters": {
                        "type": "object",
                        "properties": {"subscription_id": {"type": "string"}},
                        "required": ["subscription_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sub_check",
                    "description": (
                        "Manually poll one subscription or all enabled subscriptions. "
                        "Omit subscription_id when checking all subscriptions."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"subscription_id": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sub_status",
                    "description": "Return overall subscription system status.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "sub_add":
                return await self._sub_add(params)
            if tool_name == "sub_search":
                return await self._sub_search(params)
            if tool_name == "sub_list":
                return SkillOutput(status="success", result={"items": await self.manager.list_subscriptions()})
            if tool_name == "sub_remove":
                removed = await self.manager.remove_subscription(str(params.get("subscription_id") or "").strip())
                return SkillOutput(status="success", result={"removed": removed})
            if tool_name == "sub_check":
                result = await self.manager.check_subscriptions(
                    subscription_id=_normalize_subscription_check_target(params.get("subscription_id"))
                )
                return SkillOutput(status="success", result=result)
            if tool_name == "sub_status":
                return SkillOutput(status="success", result=await self.manager.get_status())
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def _sub_add(self, params: dict[str, Any]) -> SkillOutput:
        platform = str(params.get("platform") or "").strip()
        raw_target_id = str(params.get("target_id") or "").strip()
        subscription_name = str(params.get("name") or "").strip()
        interval_minutes = int(params.get("interval_minutes") or 10)
        fetcher_key = str(params.get("fetcher_key") or "").strip() or None
        session_id = str(params.get("__session_id") or "").strip() or None
        if not platform:
            return SkillOutput(status="error", error_info="platform is required")
        if not raw_target_id:
            return SkillOutput(status="error", error_info="target_id is required")

        resolved: ResolvedTarget | None = None
        target_id = raw_target_id
        if not is_direct_platform_target(platform, raw_target_id):
            resolved = await self._resolve_target(platform, raw_target_id)
            if resolved is not None and _query_token_length(raw_target_id) < 3:
                candidates = await self._search_candidates(platform, raw_target_id)
                if candidates:
                    logger.info(
                        "subscription.search.confirmation_required",
                        platform=platform,
                        query=raw_target_id,
                        target_id=resolved.target_id,
                        candidate_count=len(candidates),
                    )
                    return SkillOutput(
                        status="success",
                        result=_format_candidates(platform, candidates),
                        metadata={
                            "requires_confirmation": True,
                            "platform": platform,
                            "query": raw_target_id,
                            "candidates": [
                                {
                                    "platform_id": candidate.platform_id,
                                    "name": candidate.name,
                                    "description": candidate.description,
                                    "followers": candidate.followers,
                                    "recent_work": candidate.recent_work,
                                }
                                for candidate in candidates
                            ],
                        },
                    )
            if resolved is None:
                candidates = await self._search_candidates(platform, raw_target_id)
                if candidates:
                    logger.info(
                        "subscription.search.prompt_returned",
                        platform=platform,
                        keyword=raw_target_id,
                        candidate_count=len(candidates),
                    )
                    return SkillOutput(
                        status="success",
                        result=_format_candidates(platform, candidates),
                        metadata={
                            "requires_confirmation": True,
                            "platform": platform,
                            "query": raw_target_id,
                            "candidates": [
                                {
                                    "platform_id": candidate.platform_id,
                                    "name": candidate.name,
                                    "description": candidate.description,
                                    "followers": candidate.followers,
                                    "recent_work": candidate.recent_work,
                                }
                                for candidate in candidates
                            ],
                        },
                    )
                return SkillOutput(
                    status="success",
                    result=(
                        f"\u672a\u627e\u5230 {platform} \u4e0a\u5339\u914d '{raw_target_id}' \u7684\u7528\u6237\uff0c"
                        "\u8bf7\u63d0\u4f9b\u66f4\u51c6\u786e\u7684\u540d\u5b57\u6216\u76f4\u63a5\u63d0\u4f9b\u5e73\u53f0 ID\u3002"
                    ),
                    metadata={
                        "requires_confirmation": False,
                        "platform": platform,
                        "query": raw_target_id,
                        "candidates": [],
                    },
                )
            target_id = resolved.target_id
            if not subscription_name:
                subscription_name = resolved.canonical_name

        if not subscription_name:
            subscription_name = raw_target_id

        created = await self.manager.add_subscription(
            platform=platform,
            target_id=target_id,
            name=subscription_name,
            interval_minutes=interval_minutes,
            fetcher_key=fetcher_key,
            session_id=session_id,
            bootstrap=True,
        )
        return SkillOutput(
            status="success",
            result={
                "id": created["id"],
                "name": created["name"],
                "platform": created["platform"],
                "fetcher_key": created.get("fetcher_key"),
            },
        )

    async def _sub_search(self, params: dict[str, Any]) -> SkillOutput:
        platform = str(params.get("platform") or "").strip()
        keyword = str(params.get("keyword") or "").strip()
        if not platform:
            return SkillOutput(status="error", error_info="platform is required")
        if not keyword:
            return SkillOutput(status="error", error_info="keyword is required")
        candidates = await self._search_candidates(platform, keyword)
        if not candidates:
            return SkillOutput(
                status="success",
                result=f"\u672a\u627e\u5230 {platform} \u4e0a\u5339\u914d '{keyword}' \u7684\u7528\u6237",
            )
        return SkillOutput(
            status="success",
            result=_format_candidates(platform, candidates),
            metadata={
                "requires_confirmation": False,
                "platform": platform,
                "query": keyword,
                "candidates": [
                    {
                        "platform_id": candidate.platform_id,
                        "name": candidate.name,
                        "description": candidate.description,
                        "followers": candidate.followers,
                        "recent_work": candidate.recent_work,
                    }
                    for candidate in candidates
                ],
            },
        )

    async def _resolve_target(self, platform: str, target_id: str) -> ResolvedTarget | None:
        resolver = getattr(self.manager, "target_resolver", None)
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            return None
        resolved = await resolver.resolve(platform, target_id)
        if resolved is not None:
            logger.info(
                "subscription.search.exact_match",
                platform=platform,
                query=target_id,
                target_id=resolved.target_id,
            )
        return resolved

    async def _search_candidates(self, platform: str, keyword: str) -> list[SearchCandidate]:
        resolver = getattr(self.manager, "target_resolver", None)
        if resolver is None or not callable(getattr(resolver, "search_candidates", None)):
            return []
        return await resolver.search_candidates(platform, keyword, limit=5)
