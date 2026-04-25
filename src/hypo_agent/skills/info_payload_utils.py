from __future__ import annotations

from typing import Any, Iterable

_TEXT_KEYS = (
    "text",
    "value",
    "name",
    "title",
    "headline",
    "summary",
    "brief",
    "digest",
    "description",
    "excerpt",
    "label",
    "content",
)


def first_text_from_paths(payload: dict[str, Any], *paths: Iterable[str]) -> str:
    for path in paths:
        value = _value_at_path(payload, tuple(path))
        text = coerce_text(value)
        if text:
            return text
    return ""


def coerce_text(value: Any) -> str:
    return _coerce_text(value, depth=0)


def _coerce_text(value: Any, *, depth: int) -> str:
    if depth > 3 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        parts: list[str] = []
        seen: set[str] = set()
        for item in value[:4]:
            text = _coerce_text(item, depth=depth + 1)
            if not text or text in seen:
                continue
            parts.append(text)
            seen.add(text)
        return "；".join(parts)
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            if key in value:
                text = _coerce_text(value.get(key), depth=depth + 1)
                if text:
                    return text
        if len(value) == 1:
            only_value = next(iter(value.values()))
            return _coerce_text(only_value, depth=depth + 1)
    return ""


def _value_at_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
