from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StructuredStore:
    def __init__(self, db_path: Path | str = "memory/hypo.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # TODO(M9+): consider connection reuse/pooling to reduce per-call connects.
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS preferences (
                        pref_key TEXT PRIMARY KEY,
                        pref_value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS token_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        requested_model TEXT NOT NULL,
                        resolved_model TEXT NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        total_tokens INTEGER,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.commit()

            self._initialized = True

    async def upsert_session(self, session_id: str) -> None:
        await self.init()
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions(session_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at=excluded.updated_at
                """,
                (session_id, now, now),
            )
            await db.commit()

    async def list_sessions(self) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT session_id, created_at, updated_at
                FROM sessions
                ORDER BY updated_at DESC, session_id DESC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def set_preference(self, key: str, value: str) -> None:
        await self.init()
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO preferences(pref_key, pref_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(pref_key) DO UPDATE SET
                    pref_value=excluded.pref_value,
                    updated_at=excluded.updated_at
                """,
                (key, value, now),
            )
            await db.commit()

    async def get_preference(self, key: str) -> str | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT pref_value FROM preferences WHERE pref_key = ?",
                (key,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    async def record_token_usage(
        self,
        *,
        session_id: str,
        requested_model: str,
        resolved_model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        await self.init()
        await self.upsert_session(session_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO token_usage(
                    session_id,
                    requested_model,
                    resolved_model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    requested_model,
                    resolved_model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    _now_iso(),
                ),
            )
            await db.commit()

    async def list_token_usage(
        self,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if session_id is None:
                query = """
                    SELECT id, session_id, requested_model, resolved_model,
                           input_tokens, output_tokens, total_tokens, created_at
                    FROM token_usage
                    ORDER BY id DESC
                """
                params: tuple[Any, ...] = ()
            else:
                query = """
                    SELECT id, session_id, requested_model, resolved_model,
                           input_tokens, output_tokens, total_tokens, created_at
                    FROM token_usage
                    WHERE session_id = ?
                    ORDER BY id DESC
                """
                params = (session_id,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]
