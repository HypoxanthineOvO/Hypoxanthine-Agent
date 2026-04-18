from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import structlog


logger = structlog.get_logger("hypo_agent.core.antigravity_compat")

_ANTIGRAVITY_PROVIDER_NAMES = {
    "vsplab_gemini",
    "vsplab_claude",
}

ANTIGRAVITY_RESERVED_TOOL_NAMES = {
    "web_search",
    "computer",
    "bash",
    "code_execution",
    "text_editor",
    "str_replace_editor",
    "google_search",
}

_TOOL_NAME_REWRITE_MAP: dict[str, str] = {}


@dataclass(slots=True)
class AntigravityToolTransformResult:
    tools: list[dict[str, Any]]
    renamed_tools: list[dict[str, str]]
    reverse_name_map: dict[str, str]


@dataclass(slots=True)
class AntigravityToolAuditRow:
    name: str
    status: str
    replacement: str | None = None


def is_antigravity_provider(provider_name: str | None) -> bool:
    normalized = str(provider_name or "").strip().lower()
    return normalized in _ANTIGRAVITY_PROVIDER_NAMES


def audit_antigravity_tool_names(tool_names: list[str]) -> list[AntigravityToolAuditRow]:
    rows: list[AntigravityToolAuditRow] = []
    for raw_name in tool_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        replacement = _TOOL_NAME_REWRITE_MAP.get(name)
        if name in ANTIGRAVITY_RESERVED_TOOL_NAMES:
            rows.append(
                AntigravityToolAuditRow(
                    name=name,
                    status="collision",
                    replacement=replacement,
                )
            )
            continue
        rows.append(AntigravityToolAuditRow(name=name, status="clean"))
    return rows


def log_antigravity_tool_name_audit(tool_names: list[str]) -> list[AntigravityToolAuditRow]:
    rows = audit_antigravity_tool_names(tool_names)
    collisions = [row for row in rows if row.status == "collision"]
    if collisions:
        for row in collisions:
            logger.warning(
                "Antigravity compat: tool name collides with reserved name",
                tool_name=row.name,
                replacement=row.replacement,
            )
        return rows
    logger.info("Antigravity compat: all tool names clean ✓", tool_count=len(rows))
    return rows


def transform_antigravity_tools(
    tools: list[dict[str, Any]] | None,
) -> AntigravityToolTransformResult:
    if not tools:
        return AntigravityToolTransformResult(
            tools=[],
            renamed_tools=[],
            reverse_name_map={},
        )

    transformed_tools: list[dict[str, Any]] = []
    renamed_tools: list[dict[str, str]] = []
    reverse_name_map: dict[str, str] = {}

    for tool in tools:
        updated_tool = deepcopy(tool)
        function_payload = updated_tool.get("function")
        if not isinstance(function_payload, dict):
            transformed_tools.append(updated_tool)
            continue
        original_name = str(function_payload.get("name") or "").strip()
        if not original_name:
            transformed_tools.append(updated_tool)
            continue
        replacement_name = _TOOL_NAME_REWRITE_MAP.get(original_name, original_name)
        if replacement_name != original_name:
            updated_tool["function"] = {
                **function_payload,
                "name": replacement_name,
            }
            renamed_tools.append(
                {
                    "original_name": original_name,
                    "sanitized_name": replacement_name,
                }
            )
            reverse_name_map[replacement_name] = original_name
        transformed_tools.append(updated_tool)

    return AntigravityToolTransformResult(
        tools=transformed_tools,
        renamed_tools=renamed_tools,
        reverse_name_map=reverse_name_map,
    )


def restore_antigravity_tool_call_names(
    tool_calls: list[dict[str, Any]] | None,
    reverse_name_map: dict[str, str],
) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    if not reverse_name_map:
        return [deepcopy(item) for item in tool_calls]

    restored: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        updated_tool_call = deepcopy(tool_call)
        function_payload = updated_tool_call.get("function")
        if isinstance(function_payload, dict):
            current_name = str(function_payload.get("name") or "").strip()
            original_name = reverse_name_map.get(current_name)
            if original_name:
                updated_tool_call["function"] = {
                    **function_payload,
                    "name": original_name,
                }
        restored.append(updated_tool_call)
    return restored
