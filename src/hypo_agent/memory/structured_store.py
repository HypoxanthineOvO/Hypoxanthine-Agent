from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import aiosqlite
try:
    import sqlite_vec
except ImportError:  # pragma: no cover - depends on runtime environment
    sqlite_vec = None
import structlog

from hypo_agent.core.config_loader import get_database_path

logger = structlog.get_logger("hypo_agent.memory.structured_store")
_FTS5_SPECIAL_CHARS_RE = re.compile(r'["*():^]')
_FTS5_TERM_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")
_CJK_TERM_RE = re.compile(r"^[\u3400-\u9fff]+$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_search_text(value: str) -> str:
    normalized: list[str] = []
    for char in value:
        if char.isascii():
            normalized.append(char.lower())
        elif char.strip():
            normalized.append(char)
        else:
            normalized.append(" ")
    return " ".join(token for token in "".join(normalized).split() if token)


def _quote_fts5_term(term: str) -> str:
    return f'"{term.replace("\"", "\"\"")}"'


def _expand_cjk_search_terms(term: str) -> list[str]:
    if not _CJK_TERM_RE.fullmatch(term):
        return [term]
    if len(term) <= 4:
        return [term]

    expanded = [term]
    expanded.extend(term[idx : idx + 2] for idx in range(len(term) - 1))
    return expanded


def _build_fts5_match_query(query: str) -> str:
    normalized_query = _normalize_search_text(query)
    if not normalized_query:
        return ""

    sanitized = _FTS5_SPECIAL_CHARS_RE.sub(" ", normalized_query)
    raw_terms = _FTS5_TERM_RE.findall(sanitized)
    expanded_terms: list[str] = []
    for idx, term in enumerate(raw_terms):
        stripped = term.strip()
        if not stripped:
            continue
        expanded_terms.extend(_expand_cjk_search_terms(stripped))
        if idx + 1 < len(raw_terms):
            next_term = raw_terms[idx + 1].strip()
            if (
                next_term
                and _CJK_TERM_RE.fullmatch(stripped)
                and _CJK_TERM_RE.fullmatch(next_term)
            ):
                expanded_terms.append(stripped + next_term)

    unique_terms = [term for term in dict.fromkeys(expanded_terms) if term]
    if not unique_terms:
        return ""
    return " OR ".join(_quote_fts5_term(term) for term in unique_terms[:24])


def _deserialize_embedding_blob(blob: bytes | bytearray | memoryview | str | None) -> list[float]:
    if blob is None:
        return []
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(blob).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
    elif isinstance(blob, str):
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            return []
    else:
        return []
    if not isinstance(payload, list):
        return []
    return [float(item) for item in payload]


def _squared_l2_distance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return float("inf")
    return sum((float(a) - float(b)) ** 2 for a, b in zip(left, right, strict=True))


class StructuredStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = get_database_path()

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # TODO(M9+): consider connection reuse/pooling to reduce per-call connects.
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _load_sqlite_vec(self, db: aiosqlite.Connection) -> None:
        if sqlite_vec is None:
            return
        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        await db.enable_load_extension(False)

    def _load_sqlite_vec_sync(self, db: sqlite3.Connection) -> None:
        if sqlite_vec is None:
            return
        db.enable_load_extension(True)
        db.load_extension(sqlite_vec.loadable_path())
        db.enable_load_extension(False)

    async def init(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            async with aiosqlite.connect(self.db_path) as db:
                await self._load_sqlite_vec(db)
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        gc_processed INTEGER NOT NULL DEFAULT 0
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
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT,
                        schedule_type TEXT NOT NULL,
                        schedule_value TEXT NOT NULL,
                        channel TEXT NOT NULL DEFAULT 'all',
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        next_run_at TEXT,
                        heartbeat_config TEXT
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_reminders_status
                    ON reminders(status)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_reminders_next_run
                    ON reminders(next_run_at)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_emails (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_name TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        subject TEXT,
                        sender TEXT,
                        received_at TEXT,
                        category TEXT,
                        summary TEXT,
                        attachment_paths TEXT,
                        processed_at TEXT NOT NULL,
                        UNIQUE(account_name, message_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_processed_emails_account_message
                    ON processed_emails(account_name, message_id)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS semantic_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        chunk_text TEXT NOT NULL,
                        embedding BLOB NOT NULL,
                        file_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_semantic_chunks_file
                    ON semantic_chunks(file_path)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS semantic_index_meta (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                if sqlite_vec is not None:
                    await db.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS semantic_chunks_vec
                        USING vec0(id INTEGER PRIMARY KEY, embedding float[1])
                        """
                    )
                else:
                    await db.execute(
                        """
                        CREATE TABLE IF NOT EXISTS semantic_chunks_vec (
                            id INTEGER PRIMARY KEY,
                            embedding BLOB NOT NULL
                        )
                        """
                    )
                await db.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_chunks_fts
                    USING fts5(id UNINDEXED, file_path, chunk_text)
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
                async with db.execute("PRAGMA table_info(sessions)") as cursor:
                    session_columns = await cursor.fetchall()
                session_column_names = {str(column[1]) for column in session_columns}
                if "gc_processed" not in session_column_names:
                    await db.execute(
                        "ALTER TABLE sessions ADD COLUMN gc_processed INTEGER NOT NULL DEFAULT 0"
                    )
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
                SELECT session_id, created_at, updated_at, gc_processed
                FROM sessions
                ORDER BY updated_at DESC, session_id DESC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def is_session_gc_processed(self, session_id: str) -> bool:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT gc_processed FROM sessions WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return False
        return bool(int(row[0] or 0))

    async def mark_session_gc_processed(
        self,
        session_id: str,
        processed: bool = True,
    ) -> None:
        await self.init()
        await self.upsert_session(session_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE sessions
                SET gc_processed = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (1 if processed else 0, _now_iso(), session_id),
            )
            await db.commit()

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

    async def save_preference(self, key: str, value: str) -> None:
        await self.set_preference(key, value)

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

    def list_preferences_sync(self, limit: int = 20) -> list[tuple[str, str]]:
        if limit <= 0:
            return []
        if not self.db_path.exists():
            return []

        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT pref_key, pref_value
                    FROM preferences
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
        except (sqlite3.Error, TypeError, ValueError):
            return []

        result: list[tuple[str, str]] = []
        for row in rows:
            if not row or len(row) < 2:
                continue
            key = str(row[0] or "").strip()
            if not key:
                continue
            value = str(row[1] or "")
            result.append((key, value))
        return result

    async def ensure_semantic_vector_dimensions(self, dimensions: int) -> None:
        await self.init()
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")

        async with aiosqlite.connect(self.db_path) as db:
            await self._load_sqlite_vec(db)
            async with db.execute(
                "SELECT meta_value FROM semantic_index_meta WHERE meta_key = 'embedding_dimensions'"
            ) as cursor:
                row = await cursor.fetchone()
            current = str(row[0]) if row is not None else None
            if current == str(dimensions):
                return

            if current is not None and current != str(dimensions):
                await db.execute("DELETE FROM semantic_chunks")
                await db.execute("DELETE FROM semantic_chunks_fts")

            await db.execute("DROP TABLE IF EXISTS semantic_chunks_vec")
            if sqlite_vec is not None:
                await db.execute(
                    f"""
                    CREATE VIRTUAL TABLE semantic_chunks_vec
                    USING vec0(id INTEGER PRIMARY KEY, embedding float[{int(dimensions)}])
                    """
                )
            else:
                await db.execute(
                    """
                    CREATE TABLE semantic_chunks_vec (
                        id INTEGER PRIMARY KEY,
                        embedding BLOB NOT NULL
                    )
                    """
                )
            await db.execute(
                """
                INSERT INTO semantic_index_meta(meta_key, meta_value, updated_at)
                VALUES ('embedding_dimensions', ?, ?)
                ON CONFLICT(meta_key) DO UPDATE SET
                    meta_value=excluded.meta_value,
                    updated_at=excluded.updated_at
                """,
                (str(dimensions), _now_iso()),
            )
            await db.commit()

    async def get_semantic_dimensions(self) -> int | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT meta_value FROM semantic_index_meta WHERE meta_key = 'embedding_dimensions'"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None

    async def list_semantic_files(self) -> list[str]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT DISTINCT file_path FROM semantic_chunks ORDER BY file_path"
            ) as cursor:
                rows = await cursor.fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

    async def get_semantic_file_hash(self, file_path: str) -> str | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT file_hash
                FROM semantic_chunks
                WHERE file_path = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (file_path,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return str(row[0] or "")

    async def delete_semantic_chunks(self, file_path: str) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await self._load_sqlite_vec(db)
            async with db.execute(
                "SELECT id FROM semantic_chunks WHERE file_path = ? ORDER BY id",
                (file_path,),
            ) as cursor:
                rows = await cursor.fetchall()
            ids = [int(row[0]) for row in rows if row and row[0] is not None]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                await db.execute(
                    f"DELETE FROM semantic_chunks_vec WHERE id IN ({placeholders})",
                    tuple(ids),
                )
                await db.execute(
                    f"DELETE FROM semantic_chunks_fts WHERE id IN ({placeholders})",
                    tuple(ids),
                )
            await db.execute("DELETE FROM semantic_chunks WHERE file_path = ?", (file_path,))
            await db.commit()

    async def replace_semantic_chunks(
        self,
        *,
        file_path: str,
        file_hash: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await self._load_sqlite_vec(db)
            async with db.execute(
                "SELECT id FROM semantic_chunks WHERE file_path = ? ORDER BY id",
                (file_path,),
            ) as cursor:
                old_rows = await cursor.fetchall()
            old_ids = [int(row[0]) for row in old_rows if row and row[0] is not None]
            if old_ids:
                placeholders = ",".join("?" for _ in old_ids)
                await db.execute(
                    f"DELETE FROM semantic_chunks_vec WHERE id IN ({placeholders})",
                    tuple(old_ids),
                )
                await db.execute(
                    f"DELETE FROM semantic_chunks_fts WHERE id IN ({placeholders})",
                    tuple(old_ids),
                )
            await db.execute("DELETE FROM semantic_chunks WHERE file_path = ?", (file_path,))

            created_at = _now_iso()
            for chunk in chunks:
                cursor = await db.execute(
                    """
                    INSERT INTO semantic_chunks(
                        file_path,
                        chunk_index,
                        chunk_text,
                        embedding,
                        file_hash,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_path,
                        int(chunk["chunk_index"]),
                        str(chunk["chunk_text"]),
                        chunk["embedding_blob"],
                        file_hash,
                        created_at,
                    ),
                )
                chunk_id = int(cursor.lastrowid)
                await db.execute(
                    "INSERT INTO semantic_chunks_vec(id, embedding) VALUES (?, ?)",
                    (chunk_id, chunk["embedding_blob"]),
                )
                await db.execute(
                    """
                    INSERT INTO semantic_chunks_fts(id, file_path, chunk_text)
                    VALUES (?, ?, ?)
                    """,
                    (
                        chunk_id,
                        file_path,
                        _normalize_search_text(str(chunk["chunk_text"])),
                    ),
                )
            await db.commit()

    async def semantic_vector_search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        await self.init()
        if not query_embedding or limit <= 0:
            return []

        current_dimensions = await self.get_semantic_dimensions()
        if current_dimensions is None or current_dimensions != len(query_embedding):
            return []

        async with aiosqlite.connect(self.db_path) as db:
            await self._load_sqlite_vec(db)
            db.row_factory = aiosqlite.Row
            if sqlite_vec is not None:
                async with db.execute(
                    """
                    SELECT sc.id, sc.file_path, sc.chunk_index, sc.chunk_text, v.distance
                    FROM (
                        SELECT id, distance
                        FROM semantic_chunks_vec
                        WHERE embedding MATCH ? AND k = ?
                        ORDER BY distance
                    ) AS v
                    JOIN semantic_chunks AS sc ON sc.id = v.id
                    ORDER BY v.distance
                    """,
                    (json.dumps(query_embedding, ensure_ascii=False), int(limit)),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]

            async with db.execute(
                """
                SELECT sc.id, sc.file_path, sc.chunk_index, sc.chunk_text, v.embedding
                FROM semantic_chunks_vec AS v
                JOIN semantic_chunks AS sc ON sc.id = v.id
                """
            ) as cursor:
                rows = await cursor.fetchall()

        scored: list[dict[str, Any]] = []
        for row in rows:
            embedding = _deserialize_embedding_blob(row[4])
            if not embedding:
                continue
            scored.append(
                {
                    "id": int(row[0]),
                    "file_path": str(row[1]),
                    "chunk_index": int(row[2]),
                    "chunk_text": str(row[3]),
                    "distance": _squared_l2_distance(query_embedding, embedding),
                }
            )
        scored.sort(key=lambda item: (float(item["distance"]), item["file_path"], item["chunk_index"]))
        return scored[: int(limit)]

    async def semantic_keyword_search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        await self.init()
        if limit <= 0:
            return []

        match_query = _build_fts5_match_query(query)
        logger.debug("fts5.match_query", query=query, sanitized=match_query)
        if not match_query:
            return []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    """
                    SELECT sc.id, sc.file_path, sc.chunk_index, sc.chunk_text,
                           bm25(semantic_chunks_fts) AS bm25_score
                    FROM semantic_chunks_fts
                    JOIN semantic_chunks AS sc ON sc.id = semantic_chunks_fts.id
                    WHERE semantic_chunks_fts MATCH ?
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (match_query, int(limit)),
                ) as cursor:
                    rows = await cursor.fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "fts5.match_failed",
                    query=query,
                    sanitized=match_query,
                    error=str(exc),
                )
                return []
        return [dict(row) for row in rows]

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

    async def create_reminder(
        self,
        *,
        title: str,
        description: str | None,
        schedule_type: str,
        schedule_value: str,
        channel: str = "all",
        status: str = "active",
        next_run_at: str | None = None,
        heartbeat_config: str | None = None,
    ) -> int:
        await self.init()
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO reminders(
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    channel,
                    status,
                    created_at,
                    updated_at,
                    next_run_at,
                    heartbeat_config
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    channel,
                    status,
                    now,
                    now,
                    next_run_at,
                    heartbeat_config,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    channel,
                    status,
                    created_at,
                    updated_at,
                    next_run_at,
                    heartbeat_config
                FROM reminders
                WHERE id = ?
                """,
                (reminder_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        payload = dict(row)
        payload["heartbeat_config"] = self._normalize_heartbeat_config(
            payload.get("heartbeat_config")
        )
        return payload

    async def list_reminders(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT
                    id,
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    channel,
                    status,
                    created_at,
                    updated_at,
                    next_run_at,
                    heartbeat_config
                FROM reminders
            """
            params: tuple[Any, ...] = ()
            status_filter = (
                str(status).strip().lower()
                if status is not None
                else None
            )
            if status_filter in {"", "all"}:
                status_filter = None
            if status_filter is None:
                query += " WHERE LOWER(TRIM(status)) != 'deleted'"
            else:
                query += " WHERE LOWER(TRIM(status)) = ?"
                params = (status_filter,)
            query += " ORDER BY id DESC"
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        payloads: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["heartbeat_config"] = self._normalize_heartbeat_config(
                payload.get("heartbeat_config")
            )
            payloads.append(payload)
        return payloads

    async def update_reminder(
        self,
        reminder_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        schedule_type: str | None = None,
        schedule_value: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        next_run_at: str | None = None,
        heartbeat_config: str | None = None,
    ) -> None:
        await self.init()
        fields: list[str] = []
        params: list[Any] = []

        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if description is not None:
            fields.append("description = ?")
            params.append(description)
        if schedule_type is not None:
            fields.append("schedule_type = ?")
            params.append(schedule_type)
        if schedule_value is not None:
            fields.append("schedule_value = ?")
            params.append(schedule_value)
        if channel is not None:
            fields.append("channel = ?")
            params.append(channel)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if next_run_at is not None:
            fields.append("next_run_at = ?")
            params.append(next_run_at)
        if heartbeat_config is not None:
            fields.append("heartbeat_config = ?")
            params.append(heartbeat_config)

        fields.append("updated_at = ?")
        params.append(_now_iso())
        params.append(reminder_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE reminders
                SET {", ".join(fields)}
                WHERE id = ?
                """,
                tuple(params),
            )
            await db.commit()

    async def delete_reminder(self, reminder_id: int) -> None:
        await self.update_reminder(reminder_id, status="deleted")

    async def mark_reminder_completed(self, reminder_id: int) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE reminders
                SET status = 'completed', next_run_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_now_iso(), reminder_id),
            )
            await db.commit()

    async def set_reminder_next_run_at(
        self,
        reminder_id: int,
        next_run_at: str | None,
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE reminders
                SET next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_run_at, _now_iso(), reminder_id),
            )
            await db.commit()

    def _normalize_heartbeat_config(
        self,
        value: Any,
    ) -> list[dict[str, Any]] | None:
        if value in (None, ""):
            return None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        return None

    async def insert_processed_email(
        self,
        *,
        account_name: str,
        message_id: str,
        subject: str | None = None,
        sender: str | None = None,
        received_at: str | None = None,
        category: str | None = None,
        summary: str | None = None,
        attachment_paths: list[str] | None = None,
    ) -> bool:
        await self.init()
        account = account_name.strip()
        message = message_id.strip()
        if not account or not message:
            return False

        attachments_json = json.dumps(attachment_paths or [], ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO processed_emails(
                    account_name,
                    message_id,
                    subject,
                    sender,
                    received_at,
                    category,
                    summary,
                    attachment_paths,
                    processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account,
                    message,
                    subject,
                    sender,
                    received_at,
                    category,
                    summary,
                    attachments_json,
                    _now_iso(),
                ),
            )
            await db.commit()
            return (cursor.rowcount or 0) > 0

    async def has_processed_email(
        self,
        account_name: str,
        message_id: str,
    ) -> bool:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT 1
                FROM processed_emails
                WHERE account_name = ? AND message_id = ?
                LIMIT 1
                """,
                (account_name.strip(), message_id.strip()),
            ) as cursor:
                row = await cursor.fetchone()
        return row is not None

    async def list_overdue_pending_reminders(self, *, limit: int = 20) -> list[dict[str, Any]]:
        await self.init()
        safe_limit = max(1, int(limit))
        now_iso = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    status,
                    next_run_at
                FROM reminders
                WHERE LOWER(TRIM(COALESCE(status, ''))) IN ('active', 'pending')
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC, id ASC
                LIMIT ?
                """,
                (now_iso, safe_limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]
