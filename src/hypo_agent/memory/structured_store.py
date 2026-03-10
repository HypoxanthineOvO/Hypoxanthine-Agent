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
                        latency_ms REAL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_invocations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        skill_name TEXT,
                        params_json TEXT,
                        status TEXT NOT NULL,
                        result_summary TEXT,
                        duration_ms REAL,
                        error_info TEXT,
                        compressed_meta_json TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tool_invocations_session
                    ON tool_invocations(session_id)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tool_invocations_tool
                    ON tool_invocations(tool_name)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tool_invocations_created
                    ON tool_invocations(created_at)
                    """
                )
                async with db.execute("PRAGMA table_info(token_usage)") as cursor:
                    columns = await cursor.fetchall()
                column_names = {str(column[1]) for column in columns}
                if "latency_ms" not in column_names:
                    await db.execute("ALTER TABLE token_usage ADD COLUMN latency_ms REAL")

                async with db.execute("PRAGMA table_info(tool_invocations)") as cursor:
                    tool_columns = await cursor.fetchall()
                tool_column_names = {str(column[1]) for column in tool_columns}
                if "skill_name" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN skill_name TEXT")
                if "params_json" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN params_json TEXT")
                if "result_summary" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN result_summary TEXT")
                if "compressed_meta_json" not in tool_column_names:
                    await db.execute(
                        "ALTER TABLE tool_invocations ADD COLUMN compressed_meta_json TEXT"
                    )

                # Backfill from legacy columns when they exist.
                if "params" in tool_column_names:
                    await db.execute(
                        """
                        UPDATE tool_invocations
                        SET params_json = params
                        WHERE params_json IS NULL AND params IS NOT NULL
                        """
                    )
                if "result_preview" in tool_column_names:
                    await db.execute(
                        """
                        UPDATE tool_invocations
                        SET result_summary = result_preview
                        WHERE result_summary IS NULL AND result_preview IS NOT NULL
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
        latency_ms: float | None = None,
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
                    latency_ms,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    requested_model,
                    resolved_model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    latency_ms,
                    _now_iso(),
                ),
            )
            await db.commit()

    async def list_token_usage(
        self,
        session_id: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            clauses: list[str] = []
            params: list[Any] = []
            if session_id is not None:
                clauses.append("session_id = ?")
                params.append(session_id)
            if since_iso is not None:
                clauses.append("created_at >= ?")
                params.append(since_iso)

            query = """
                SELECT id, session_id, requested_model, resolved_model,
                       input_tokens, output_tokens, total_tokens, latency_ms, created_at
                FROM token_usage
            """
            if clauses:
                query += f" WHERE {' AND '.join(clauses)}"
            query += " ORDER BY id DESC"

            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def summarize_token_usage(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            where_clause = ""
            params: tuple[Any, ...] = ()
            if session_id is not None:
                where_clause = "WHERE session_id = ?"
                params = (session_id,)

            query = f"""
                SELECT
                    resolved_model,
                    COUNT(*) AS calls,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM token_usage
                {where_clause}
                GROUP BY resolved_model
                ORDER BY calls DESC, resolved_model ASC
            """
            async with db.execute(query, params) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]

            total_query = f"""
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM token_usage
                {where_clause}
            """
            async with db.execute(total_query, params) as cursor:
                total_row = await cursor.fetchone()

        totals = dict(total_row) if total_row is not None else {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        return {
            "session_id": session_id,
            "rows": rows,
            "totals": totals,
        }

    async def summarize_latency_by_model(self) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    resolved_model,
                    COUNT(latency_ms) AS calls,
                    MIN(latency_ms) AS min_latency_ms,
                    MAX(latency_ms) AS max_latency_ms,
                    AVG(latency_ms) AS avg_latency_ms
                FROM token_usage
                WHERE latency_ms IS NOT NULL
                GROUP BY resolved_model
                ORDER BY calls DESC, resolved_model ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def record_tool_invocation(
        self,
        *,
        session_id: str,
        tool_name: str,
        skill_name: str | None,
        params_json: str | None,
        status: str,
        result_summary: str | None,
        duration_ms: float | None,
        error_info: str | None,
        compressed_meta_json: str | None = None,
    ) -> int | None:
        await self.init()
        await self.upsert_session(session_id)
        summary = result_summary[:500] if isinstance(result_summary, str) else None
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO tool_invocations(
                    session_id,
                    tool_name,
                    skill_name,
                    params_json,
                    status,
                    result_summary,
                    duration_ms,
                    error_info,
                    compressed_meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    tool_name,
                    skill_name,
                    params_json,
                    status,
                    summary,
                    duration_ms,
                    error_info,
                    compressed_meta_json,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid) if cursor.lastrowid is not None else None

    async def update_tool_invocation_compressed_meta(
        self,
        invocation_id: int,
        *,
        compressed_meta_json: str,
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE tool_invocations
                SET compressed_meta_json = ?
                WHERE id = ?
                """,
                (compressed_meta_json, invocation_id),
            )
            await db.commit()

    async def list_tool_invocations(
        self,
        session_id: str | None = None,
        limit: int | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            clauses: list[str] = []
            params: list[Any] = []
            if session_id is not None:
                clauses.append("session_id = ?")
                params.append(session_id)
            if since_iso is not None:
                clauses.append("created_at >= ?")
                params.append(since_iso)

            query = """
                SELECT
                    id,
                    session_id,
                    tool_name,
                    skill_name,
                    params_json,
                    status,
                    result_summary,
                    duration_ms,
                    error_info,
                    compressed_meta_json,
                    created_at
                FROM tool_invocations
            """
            if clauses:
                query += f" WHERE {' AND '.join(clauses)}"
            query += " ORDER BY created_at DESC, id DESC"
            if limit is not None and limit > 0:
                query += " LIMIT ?"
                params.append(limit)

            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_session_data(self, session_id: str) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM tool_invocations WHERE session_id = ?",
                (session_id,),
            )
            await db.execute(
                "DELETE FROM token_usage WHERE session_id = ?",
                (session_id,),
            )
            await db.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()
