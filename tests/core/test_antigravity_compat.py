from __future__ import annotations

from types import SimpleNamespace

import hypo_agent.core.antigravity_compat as compat


def test_antigravity_transform_is_noop_when_tool_names_are_clean() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web.",
                "parameters": {"type": "object"},
            },
        }
    ]

    result = compat.transform_antigravity_tools(tools)

    assert result.tools == tools
    assert result.renamed_tools == []
    assert result.reverse_name_map == {}


def test_antigravity_tool_name_audit_logs_warning_for_reserved_name(monkeypatch) -> None:
    warnings: list[tuple[str, dict]] = []

    fake_logger = SimpleNamespace(
        warning=lambda event, **kwargs: warnings.append((event, kwargs)),
        info=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(compat, "logger", fake_logger)

    rows = compat.log_antigravity_tool_name_audit(["web_search", "read_file"])

    assert [row.status for row in rows] == ["collision", "clean"]
    assert warnings == [
        (
            "Antigravity compat: tool name collides with reserved name",
            {"tool_name": "web_search", "replacement": None},
        )
    ]


def test_antigravity_tool_name_audit_logs_clean_when_all_names_safe(monkeypatch) -> None:
    infos: list[tuple[str, dict]] = []

    fake_logger = SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        info=lambda event, **kwargs: infos.append((event, kwargs)),
    )
    monkeypatch.setattr(compat, "logger", fake_logger)

    rows = compat.log_antigravity_tool_name_audit(["search_web", "read_file"])

    assert [row.status for row in rows] == ["clean", "clean"]
    assert infos == [("Antigravity compat: all tool names clean ✓", {"tool_count": 2})]
