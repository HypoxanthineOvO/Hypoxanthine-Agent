from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.core.time_utils import utc_isoformat
from hypo_agent.gateway.auth import require_api_token
from hypo_agent.models import Message

router = APIRouter(prefix="/api")

WRITABLE_TABLES: dict[str, bool] = {
    "preferences": True,
}
TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TableRowUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class FileUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


def _db_path(request: Request) -> Path:
    return Path(request.app.state.structured_store.db_path)


def _knowledge_root(request: Request) -> Path:
    root = getattr(request.app.state, "knowledge_dir", get_memory_dir() / "knowledge")
    return Path(root).resolve(strict=False)


def _safe_file_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    return candidate


def _dump_message(message: Message) -> dict[str, Any]:
    payload = message.model_dump(mode="json")
    if message.timestamp is None:
        payload.pop("timestamp", None)
    return payload


async def _list_table_names(db_path: Path) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
    return [str(row[0]) for row in rows]


async def _ensure_table_exists(db_path: Path, table_name: str) -> None:
    if not TABLE_NAME_PATTERN.fullmatch(table_name):
        raise HTTPException(status_code=404, detail="Table not found")
    table_names = await _list_table_names(db_path)
    if table_name not in table_names:
        raise HTTPException(status_code=404, detail="Table not found")


@router.get("/memory/tables")
async def list_memory_tables(request: Request) -> dict[str, Any]:
    require_api_token(request)

    db_path = _db_path(request)
    table_names = await _list_table_names(db_path)
    rows: list[dict[str, Any]] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for name in table_names:
            async with db.execute(f'SELECT COUNT(*) AS row_count FROM "{name}"') as cursor:
                count_row = await cursor.fetchone()
            rows.append(
                {
                    "name": name,
                    "row_count": int(count_row["row_count"]) if count_row else 0,
                    "writable": bool(WRITABLE_TABLES.get(name, False)),
                }
            )

    return {"tables": rows}


@router.get("/memory/tables/{name}")
async def get_memory_table_rows(
    name: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    require_api_token(request)

    db_path = _db_path(request)
    await _ensure_table_exists(db_path, name)

    offset = (page - 1) * size
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f'SELECT COUNT(*) AS row_count FROM "{name}"') as cursor:
            count_row = await cursor.fetchone()
        total = int(count_row["row_count"]) if count_row else 0

        async with db.execute(
            f'SELECT rowid AS _rowid, * FROM "{name}" ORDER BY rowid DESC LIMIT ? OFFSET ?',
            (size, offset),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        fallback_id = normalized.get("id") or normalized.get("pref_key") or normalized.get("_rowid")
        normalized["id"] = fallback_id
        normalized.pop("_rowid", None)
        normalized_rows.append(normalized)

    return {
        "table": name,
        "page": page,
        "size": size,
        "total": total,
        "writable": bool(WRITABLE_TABLES.get(name, False)),
        "rows": normalized_rows,
    }


@router.put("/memory/tables/{name}/{row_id}")
async def update_memory_table_row(
    name: str,
    row_id: str,
    payload: TableRowUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    require_api_token(request)

    db_path = _db_path(request)
    await _ensure_table_exists(db_path, name)
    if not WRITABLE_TABLES.get(name, False):
        raise HTTPException(status_code=403, detail=f"Table '{name}' is not writable")

    if name == "preferences":
        if "pref_value" not in payload.values:
            raise HTTPException(status_code=422, detail="pref_value is required")
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                """
                UPDATE preferences
                SET pref_value = ?, updated_at = ?
                WHERE pref_key = ?
                """,
                (str(payload.values["pref_value"]), now, row_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Row not found")
        return {"table": name, "id": row_id, "updated": True}

    raise HTTPException(status_code=403, detail=f"Table '{name}' is not writable")


@router.get("/memory/files")
async def list_memory_files(request: Request) -> dict[str, Any]:
    require_api_token(request)

    root = _knowledge_root(request)
    root.mkdir(parents=True, exist_ok=True)
    files = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return {"root": str(root), "files": files}


@router.get("/memory/files/{file_path:path}")
async def get_memory_file(file_path: str, request: Request) -> dict[str, Any]:
    require_api_token(request)

    root = _knowledge_root(request)
    target = _safe_file_path(root, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "path": file_path,
        "content": target.read_text(encoding="utf-8"),
    }


@router.put("/memory/files/{file_path:path}")
async def put_memory_file(
    file_path: str,
    payload: FileUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    require_api_token(request)

    root = _knowledge_root(request)
    root.mkdir(parents=True, exist_ok=True)
    target = _safe_file_path(root, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")

    return {
        "path": file_path,
        "saved": True,
    }


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    request: Request,
    format: str = Query(default="json"),
):
    require_api_token(request)

    session_memory = request.app.state.session_memory
    messages: list[Message] = session_memory.get_messages(session_id)
    if format == "json":
        return {
            "session_id": session_id,
            "messages": [_dump_message(message) for message in messages],
        }
    if format == "markdown":
        lines = [f"# Session {session_id}", ""]
        for message in messages:
            sender = message.sender
            timestamp = utc_isoformat(message.timestamp)
            if timestamp:
                lines.append(f"## {sender} ({timestamp})")
            else:
                lines.append(f"## {sender}")
            lines.append("")
            lines.append(message.text or "")
            lines.append("")
        return PlainTextResponse("\n".join(lines), media_type="text/markdown")
    raise HTTPException(status_code=422, detail="format must be json or markdown")


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    require_api_token(request)

    session_memory = request.app.state.session_memory
    structured_store = request.app.state.structured_store
    session_memory.clear_session(session_id)
    await structured_store.delete_session_data(session_id)
    return {
        "session_id": session_id,
        "deleted": True,
    }
