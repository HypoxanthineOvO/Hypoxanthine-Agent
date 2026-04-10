from __future__ import annotations

import inspect
import json
from typing import Any

NOTION_TODO_DATABASE_ID_KEY = "notion.todo_database_id"
NOTION_TODO_PENDING_CANDIDATE_KEY = "notion.todo_database_candidate_pending"
NOTION_TODO_DISCOVERY_QUERY = "HYX的计划通"


async def get_bound_notion_todo_database_id(
    structured_store: Any | None,
    *,
    configured_database_id: str | None = None,
) -> str | None:
    stored = await _get_preference(structured_store, NOTION_TODO_DATABASE_ID_KEY)
    normalized = str(stored or "").strip()
    if normalized:
        return normalized
    fallback = str(configured_database_id or "").strip()
    return fallback or None


async def get_pending_notion_todo_candidate(structured_store: Any | None) -> dict[str, Any] | None:
    raw = await _get_preference(structured_store, NOTION_TODO_PENDING_CANDIDATE_KEY)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    database_id = str(payload.get("database_id") or "").strip()
    if not database_id:
        return None
    return {
        "database_id": database_id,
        "title": str(payload.get("title") or "").strip() or NOTION_TODO_DISCOVERY_QUERY,
        "url": str(payload.get("url") or "").strip(),
    }


async def discover_notion_todo_candidate(
    structured_store: Any | None,
    notion_client: Any,
    *,
    query: str = NOTION_TODO_DISCOVERY_QUERY,
) -> dict[str, Any]:
    pending = await get_pending_notion_todo_candidate(structured_store)
    if pending is not None:
        return {
            "status": "pending_confirmation",
            "candidate": pending,
            "human_summary": _render_confirmation_prompt(pending),
        }

    search = getattr(notion_client, "search", None)
    if not callable(search):
        return {
            "status": "unavailable",
            "error": "notion search is unavailable",
            "human_summary": "Notion 当前无法搜索数据库，请稍后再试。",
        }

    results = search(query, object_type="database", page_size=10)
    if inspect.isawaitable(results):
        results = await results
    rows = results if isinstance(results, list) else []
    candidates = [_normalize_candidate(item) for item in rows if isinstance(item, dict)]
    candidates = [item for item in candidates if item is not None]
    normalized_query = _normalize_binding_title(query)
    exact = [
        item
        for item in candidates
        if _normalize_binding_title(str(item.get("title") or "")) == normalized_query
    ]

    if len(exact) == 1:
        candidate = exact[0]
        await _set_preference(
            structured_store,
            NOTION_TODO_PENDING_CANDIDATE_KEY,
            json.dumps(candidate, ensure_ascii=False, sort_keys=True),
        )
        return {
            "status": "pending_confirmation",
            "candidate": candidate,
            "human_summary": _render_confirmation_prompt(candidate),
        }

    if not candidates:
        return {
            "status": "not_found",
            "error": "notion todo database not found",
            "human_summary": (
                f"我尝试在 Notion 中搜索数据库“{query}”，但没有找到可绑定的候选项。"
                "请检查名称、页面授权，或手动补齐 todo database ID。"
            ),
        }

    return {
        "status": "ambiguous",
        "error": "multiple notion todo database candidates found",
        "candidates": candidates[:3],
        "human_summary": _render_ambiguous_prompt(candidates[:3], query=query),
    }


async def confirm_pending_notion_todo_candidate(structured_store: Any | None) -> dict[str, Any] | None:
    pending = await get_pending_notion_todo_candidate(structured_store)
    if pending is None:
        return None
    await _set_preference(
        structured_store,
        NOTION_TODO_DATABASE_ID_KEY,
        str(pending.get("database_id") or "").strip(),
    )
    await _delete_preference(structured_store, NOTION_TODO_PENDING_CANDIDATE_KEY)
    return pending


async def reject_pending_notion_todo_candidate(structured_store: Any | None) -> dict[str, Any] | None:
    pending = await get_pending_notion_todo_candidate(structured_store)
    if pending is None:
        return None
    await _delete_preference(structured_store, NOTION_TODO_PENDING_CANDIDATE_KEY)
    return pending


def message_confirms_notion_todo_candidate(text: str, pending_candidate: dict[str, Any]) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if "确认绑定" not in normalized:
        return False
    title = str(pending_candidate.get("title") or "").strip()
    if not title:
        return True
    return title in normalized or "数据库" in normalized or "notion" in normalized.casefold()


def message_rejects_notion_todo_candidate(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(token in normalized for token in ("取消绑定", "不是这个", "别绑定", "不绑定"))


def _normalize_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    database_id = str(item.get("id") or "").strip()
    if not database_id:
        return None
    title = _extract_title(item) or NOTION_TODO_DISCOVERY_QUERY
    return {
        "database_id": database_id,
        "title": title,
        "url": str(item.get("url") or "").strip(),
    }


def _extract_title(item: dict[str, Any]) -> str:
    title = item.get("title")
    if isinstance(title, list):
        parts: list[str] = []
        for block in title:
            if not isinstance(block, dict):
                continue
            plain_text = str(block.get("plain_text") or "").strip()
            if plain_text:
                parts.append(plain_text)
                continue
            nested_text = block.get("text")
            if isinstance(nested_text, dict):
                content = str(nested_text.get("content") or "").strip()
                if content:
                    parts.append(content)
        return "".join(parts).strip()
    return ""


def _normalize_binding_title(value: str) -> str:
    return "".join(str(value or "").split()).casefold()


def _render_confirmation_prompt(candidate: dict[str, Any]) -> str:
    title = str(candidate.get("title") or NOTION_TODO_DISCOVERY_QUERY).strip()
    database_id = str(candidate.get("database_id") or "").strip()
    return (
        f"我发现了一个候选 Notion 待办数据库：{title}"
        f"（ID: {database_id}）。如果这就是你要的库，请直接回复“确认绑定 {title}”。"
    )


def _render_ambiguous_prompt(candidates: list[dict[str, Any]], *, query: str) -> str:
    lines = [f"我搜索了 Notion 数据库“{query}”，但命中了多个候选，暂时不自动绑定："]
    for item in candidates:
        lines.append(f"- {item.get('title') or '-'}（ID: {item.get('database_id') or '-'}）")
    lines.append("请手动确认正确的数据库，或直接补齐 todo database ID。")
    return "\n".join(lines)


async def _get_preference(structured_store: Any | None, key: str) -> str | None:
    if structured_store is None:
        return None
    getter = getattr(structured_store, "get_preference", None)
    if not callable(getter):
        return None
    value = getter(key)
    if inspect.isawaitable(value):
        value = await value
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


async def _set_preference(structured_store: Any | None, key: str, value: str) -> None:
    if structured_store is None:
        return
    setter = getattr(structured_store, "set_preference", None)
    if not callable(setter):
        setter = getattr(structured_store, "save_preference", None)
    if not callable(setter):
        return
    result = setter(key, value)
    if inspect.isawaitable(result):
        await result


async def _delete_preference(structured_store: Any | None, key: str) -> None:
    if structured_store is None:
        return
    deleter = getattr(structured_store, "delete_preference", None)
    if callable(deleter):
        result = deleter(key)
        if inspect.isawaitable(result):
            await result
        return
    await _set_preference(structured_store, key, "")
