from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

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
                    CREATE TABLE IF NOT EXISTS memory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        memory_id TEXT NOT NULL UNIQUE,
                        memory_class TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        language TEXT NOT NULL DEFAULT 'zh',
                        source TEXT NOT NULL DEFAULT '',
                        confidence REAL,
                        status TEXT NOT NULL DEFAULT 'active',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        rollback_metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memory_items_class_status
                    ON memory_items(memory_class, status, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_class_key
                    ON memory_items(memory_class, key)
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
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        signature TEXT NOT NULL,
                        message TEXT NOT NULL,
                        metadata_json TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_signature_created
                    ON alerts(signature, created_at)
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
                        outcome_class TEXT,
                        retryable INTEGER NOT NULL DEFAULT 0,
                        breaker_weight INTEGER NOT NULL DEFAULT 0,
                        side_effect_class TEXT,
                        operation TEXT,
                        trace_id TEXT,
                        user_visible_summary TEXT,
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
                    CREATE TABLE IF NOT EXISTS coder_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        working_directory TEXT NOT NULL,
                        prompt_summary TEXT,
                        model TEXT,
                        status TEXT NOT NULL,
                        attached INTEGER NOT NULL DEFAULT 0,
                        done INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_coder_tasks_session
                    ON coder_tasks(session_id)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_coder_tasks_status
                    ON coder_tasks(status)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_coder_tasks_attached
                    ON coder_tasks(session_id, attached, done, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repair_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        coder_task_id TEXT,
                        codex_thread_id TEXT,
                        retry_of_run_id TEXT,
                        issue_text TEXT NOT NULL,
                        finding_id TEXT,
                        working_directory TEXT NOT NULL,
                        status TEXT NOT NULL,
                        verification_state TEXT NOT NULL DEFAULT 'pending',
                        restart_state TEXT NOT NULL DEFAULT 'not_requested',
                        diagnostic_snapshot_json TEXT NOT NULL,
                        verify_commands_json TEXT NOT NULL DEFAULT '[]',
                        git_status_before TEXT NOT NULL DEFAULT '',
                        git_status_after TEXT NOT NULL DEFAULT '',
                        report_markdown TEXT NOT NULL DEFAULT '',
                        report_json TEXT NOT NULL DEFAULT '{}',
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repair_runs_session
                    ON repair_runs(session_id, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repair_runs_status
                    ON repair_runs(status, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repair_runs_task
                    ON repair_runs(coder_task_id)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repair_run_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repair_run_events_run
                    ON repair_run_events(run_id, created_at)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS codex_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        prompt_summary TEXT NOT NULL DEFAULT '',
                        working_directory TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        isolation_mode TEXT NOT NULL DEFAULT '',
                        thread_id TEXT NOT NULL DEFAULT '',
                        result_summary TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_codex_jobs_session
                    ON codex_jobs(session_id, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_codex_jobs_status
                    ON codex_jobs(status, updated_at)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS codex_job_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_codex_job_events_job
                    ON codex_job_events(job_id, created_at)
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
                        session_id TEXT NOT NULL DEFAULT 'main',
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
                async with db.execute("PRAGMA table_info(reminders)") as cursor:
                    reminder_columns = await cursor.fetchall()
                reminder_column_names = {str(column[1]) for column in reminder_columns}
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
                if "outcome_class" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN outcome_class TEXT")
                if "retryable" not in tool_column_names:
                    await db.execute(
                        "ALTER TABLE tool_invocations ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0"
                    )
                if "breaker_weight" not in tool_column_names:
                    await db.execute(
                        "ALTER TABLE tool_invocations ADD COLUMN breaker_weight INTEGER NOT NULL DEFAULT 0"
                    )
                if "side_effect_class" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN side_effect_class TEXT")
                if "operation" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN operation TEXT")
                if "trace_id" not in tool_column_names:
                    await db.execute("ALTER TABLE tool_invocations ADD COLUMN trace_id TEXT")
                if "user_visible_summary" not in tool_column_names:
                    await db.execute(
                        "ALTER TABLE tool_invocations ADD COLUMN user_visible_summary TEXT"
                    )
                if "session_id" not in reminder_column_names:
                    await db.execute(
                        "ALTER TABLE reminders ADD COLUMN session_id TEXT NOT NULL DEFAULT 'main'"
                    )
                async with db.execute("PRAGMA table_info(repair_runs)") as cursor:
                    repair_columns = await cursor.fetchall()
                repair_column_names = {str(column[1]) for column in repair_columns}
                if "codex_thread_id" not in repair_column_names:
                    await db.execute("ALTER TABLE repair_runs ADD COLUMN codex_thread_id TEXT")
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repair_runs_thread
                    ON repair_runs(codex_thread_id)
                    """
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

    async def delete_preference(self, key: str) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM preferences WHERE pref_key = ?",
                (key,),
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

    async def record_alert(
        self,
        *,
        category: str,
        signature: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO alerts(category, signature, message, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(category or "").strip(),
                    str(signature or "").strip(),
                    str(message or "").strip(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            await db.commit()

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

    async def save_memory_item(
        self,
        *,
        memory_class: str,
        key: str,
        value: str,
        source: str,
        language: str = "zh",
        confidence: float | None = None,
        status: str = "active",
        metadata_json: str = "{}",
        rollback_metadata_json: str = "{}",
        memory_id: str | None = None,
    ) -> str:
        await self.init()
        normalized_class = str(memory_class or "").strip()
        normalized_key = str(key or "").strip()
        if not normalized_class:
            raise ValueError("memory_class is required")
        if not normalized_key:
            raise ValueError("key is required")
        resolved_id = str(memory_id or "").strip() or f"mem-{uuid4().hex}"
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO memory_items(
                    memory_id,
                    memory_class,
                    key,
                    value,
                    language,
                    source,
                    confidence,
                    status,
                    metadata_json,
                    rollback_metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_class, key) DO UPDATE SET
                    value=excluded.value,
                    language=excluded.language,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    rollback_metadata_json=excluded.rollback_metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_id,
                    normalized_class,
                    normalized_key,
                    str(value or ""),
                    str(language or "zh").strip() or "zh",
                    str(source or "").strip(),
                    confidence,
                    str(status or "active").strip() or "active",
                    metadata_json,
                    rollback_metadata_json,
                    now,
                    now,
                ),
            )
            await db.commit()
        return resolved_id

    async def list_memory_items(
        self,
        *,
        memory_class: str | None = None,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        await self.init()
        clauses: list[str] = []
        params: list[Any] = []
        if memory_class is not None:
            clauses.append("memory_class = ?")
            params.append(memory_class)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        query = """
            SELECT
                id,
                memory_id,
                memory_class,
                key,
                value,
                language,
                source,
                confidence,
                status,
                metadata_json,
                rollback_metadata_json,
                created_at,
                updated_at
            FROM memory_items
        """
        if clauses:
            query += f" WHERE {' AND '.join(clauses)}"
        query += " ORDER BY updated_at DESC, id DESC"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    def list_prompt_memory_sync(self, limit: int = 20) -> list[tuple[str, str]]:
        if limit <= 0 or not self.db_path.exists():
            return []
        injectable_classes = ("user_profile", "interaction_policy", "knowledge_note", "sop")
        placeholders = ",".join("?" for _ in injectable_classes)
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    f"""
                    SELECT key, value
                    FROM memory_items
                    WHERE status = 'active' AND memory_class IN ({placeholders})
                    ORDER BY
                        CASE memory_class
                            WHEN 'interaction_policy' THEN 0
                            WHEN 'user_profile' THEN 1
                            WHEN 'sop' THEN 2
                            ELSE 3
                        END,
                        updated_at DESC
                    LIMIT ?
                    """,
                    (*injectable_classes, int(limit)),
                ).fetchall()
        except (sqlite3.Error, TypeError, ValueError):
            return []
        return [(str(row[0] or "").strip(), str(row[1] or "")) for row in rows if row and row[0]]

    async def clear_memory_items(self) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM memory_items")
            await db.commit()

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
        outcome_class: str | None = None,
        retryable: bool = False,
        breaker_weight: int = 0,
        side_effect_class: str | None = None,
        operation: str | None = None,
        trace_id: str | None = None,
        user_visible_summary: str | None = None,
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
                    compressed_meta_json,
                    outcome_class,
                    retryable,
                    breaker_weight,
                    side_effect_class,
                    operation,
                    trace_id,
                    user_visible_summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    outcome_class,
                    1 if retryable else 0,
                    max(0, int(breaker_weight or 0)),
                    side_effect_class,
                    operation,
                    trace_id,
                    user_visible_summary,
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
                    outcome_class,
                    retryable,
                    breaker_weight,
                    side_effect_class,
                    operation,
                    trace_id,
                    user_visible_summary,
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

    async def create_coder_task(
        self,
        *,
        task_id: str,
        session_id: str,
        working_directory: str,
        prompt_summary: str | None,
        model: str | None,
        status: str,
        attached: bool = False,
        done: bool = False,
        last_error: str = "",
    ) -> None:
        await self.init()
        await self.upsert_session(session_id)
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            if attached:
                await db.execute(
                    "UPDATE coder_tasks SET attached = 0 WHERE session_id = ?",
                    (session_id,),
                )
            await db.execute(
                """
                INSERT INTO coder_tasks(
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    attached,
                    done,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    working_directory=excluded.working_directory,
                    prompt_summary=excluded.prompt_summary,
                    model=excluded.model,
                    status=excluded.status,
                    attached=excluded.attached,
                    done=excluded.done,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    1 if attached else 0,
                    1 if done else 0,
                    last_error,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def get_coder_task(self, task_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    attached,
                    done,
                    last_error,
                    created_at,
                    updated_at
                FROM coder_tasks
                WHERE task_id = ?
                LIMIT 1
                """,
                (task_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_latest_coder_task_for_session(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    attached,
                    done,
                    last_error,
                    created_at,
                    updated_at
                FROM coder_tasks
                WHERE session_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_attached_coder_task_for_session(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    attached,
                    done,
                    last_error,
                    created_at,
                    updated_at
                FROM coder_tasks
                WHERE session_id = ? AND attached = 1 AND done = 0
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def list_coder_tasks(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            clauses: list[str] = []
            params: list[Any] = []
            if session_id is not None:
                clauses.append("session_id = ?")
                params.append(session_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)

            query = """
                SELECT
                    id,
                    task_id,
                    session_id,
                    working_directory,
                    prompt_summary,
                    model,
                    status,
                    attached,
                    done,
                    last_error,
                    created_at,
                    updated_at
                FROM coder_tasks
            """
            if clauses:
                query += f" WHERE {' AND '.join(clauses)}"
            query += " ORDER BY updated_at DESC, id DESC"
            if limit is not None and limit > 0:
                query += " LIMIT ?"
                params.append(limit)

            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def attach_coder_task(self, *, session_id: str, task_id: str) -> None:
        await self.init()
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE coder_tasks SET attached = 0, updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            await db.execute(
                """
                UPDATE coder_tasks
                SET attached = 1, done = 0, updated_at = ?
                WHERE task_id = ? AND session_id = ?
                """,
                (now, task_id, session_id),
            )
            await db.commit()

    async def detach_coder_task(self, *, session_id: str) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE coder_tasks SET attached = 0, updated_at = ? WHERE session_id = ?",
                (_now_iso(), session_id),
            )
            await db.commit()

    async def mark_coder_task_done(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        await self.init()
        if session_id is None and task_id is None:
            raise ValueError("session_id or task_id is required")

        clauses: list[str] = []
        params: list[Any] = [_now_iso()]
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE coder_tasks
                SET done = 1, attached = 0, updated_at = ?
                WHERE {' AND '.join(clauses)}
                """,
                tuple(params),
            )
            await db.commit()

    async def update_coder_task_status(
        self,
        *,
        task_id: str,
        status: str,
        last_error: str | None = None,
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE coder_tasks
                SET status = ?, last_error = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (status, str(last_error or ""), _now_iso(), task_id),
            )
            await db.commit()

    async def create_codex_job(
        self,
        *,
        job_id: str,
        session_id: str,
        operation: str,
        prompt_summary: str,
        working_directory: str,
        trace_id: str,
        status: str,
        isolation_mode: str,
        thread_id: str = "",
        result_summary: str = "",
        last_error: str = "",
    ) -> None:
        await self.init()
        await self.upsert_session(session_id)
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO codex_jobs(
                    job_id,
                    session_id,
                    operation,
                    prompt_summary,
                    working_directory,
                    trace_id,
                    status,
                    isolation_mode,
                    thread_id,
                    result_summary,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    operation=excluded.operation,
                    prompt_summary=excluded.prompt_summary,
                    working_directory=excluded.working_directory,
                    trace_id=excluded.trace_id,
                    status=excluded.status,
                    isolation_mode=excluded.isolation_mode,
                    thread_id=excluded.thread_id,
                    result_summary=excluded.result_summary,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    session_id,
                    operation,
                    prompt_summary[:500],
                    working_directory,
                    trace_id,
                    status,
                    isolation_mode,
                    thread_id,
                    result_summary[:1000],
                    last_error,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def update_codex_job(
        self,
        *,
        job_id: str,
        status: str,
        thread_id: str | None = None,
        result_summary: str | None = None,
        last_error: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        await self.init()
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now_iso()]
        if thread_id is not None:
            updates.append("thread_id = ?")
            params.append(thread_id)
        if result_summary is not None:
            updates.append("result_summary = ?")
            params.append(result_summary[:1000])
        if last_error is not None:
            updates.append("last_error = ?")
            params.append(last_error)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        params.append(job_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE codex_jobs SET {', '.join(updates)} WHERE job_id = ?",
                tuple(params),
            )
            await db.commit()

    async def get_codex_job(self, job_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    job_id,
                    session_id,
                    operation,
                    prompt_summary,
                    working_directory,
                    trace_id,
                    status,
                    isolation_mode,
                    thread_id,
                    result_summary,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM codex_jobs
                WHERE job_id = ?
                LIMIT 1
                """,
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def append_codex_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        summary: str = "",
        payload_json: str = "{}",
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO codex_job_events(
                    job_id,
                    event_type,
                    summary,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, event_type, summary[:1000], payload_json, _now_iso()),
            )
            await db.commit()

    async def list_codex_job_events(
        self,
        job_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        params: list[Any] = [job_id]
        query = """
            SELECT
                id,
                job_id,
                event_type,
                summary,
                payload_json,
                created_at
            FROM codex_job_events
            WHERE job_id = ?
            ORDER BY created_at ASC, id ASC
        """
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def create_repair_run(
        self,
        *,
        run_id: str,
        session_id: str,
        issue_text: str,
        working_directory: str,
        status: str,
        verification_state: str,
        restart_state: str,
        diagnostic_snapshot_json: str,
        codex_thread_id: str | None = None,
        coder_task_id: str | None = None,
        retry_of_run_id: str | None = None,
        finding_id: str | None = None,
        verify_commands_json: str = "[]",
        git_status_before: str = "",
        git_status_after: str = "",
        report_markdown: str = "",
        report_json: str = "{}",
        last_error: str = "",
        completed_at: str | None = None,
    ) -> None:
        await self.init()
        await self.upsert_session(session_id)
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO repair_runs(
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    coder_task_id=excluded.coder_task_id,
                    codex_thread_id=excluded.codex_thread_id,
                    retry_of_run_id=excluded.retry_of_run_id,
                    issue_text=excluded.issue_text,
                    finding_id=excluded.finding_id,
                    working_directory=excluded.working_directory,
                    status=excluded.status,
                    verification_state=excluded.verification_state,
                    restart_state=excluded.restart_state,
                    diagnostic_snapshot_json=excluded.diagnostic_snapshot_json,
                    verify_commands_json=excluded.verify_commands_json,
                    git_status_before=excluded.git_status_before,
                    git_status_after=excluded.git_status_after,
                    report_markdown=excluded.report_markdown,
                    report_json=excluded.report_json,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at,
                    completed_at=excluded.completed_at
                """,
                (
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    now,
                    now,
                    completed_at,
                ),
            )
            await db.commit()

    async def get_repair_run(self, run_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
                WHERE run_id = ?
                LIMIT 1
                """,
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_repair_run_by_task_id(self, coder_task_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
                WHERE coder_task_id = ? OR codex_thread_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (coder_task_id, coder_task_id),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_repair_run_by_thread_id(self, codex_thread_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
                WHERE codex_thread_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (codex_thread_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_latest_repair_run_for_session(self, session_id: str) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
                WHERE session_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_active_repair_run(self) -> dict[str, Any] | None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
                WHERE status IN ('queued', 'running')
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def list_repair_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            clauses: list[str] = []
            params: list[Any] = []
            if session_id is not None:
                clauses.append("session_id = ?")
                params.append(session_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)

            query = """
                SELECT
                    id,
                    run_id,
                    session_id,
                    coder_task_id,
                    codex_thread_id,
                    retry_of_run_id,
                    issue_text,
                    finding_id,
                    working_directory,
                    status,
                    verification_state,
                    restart_state,
                    diagnostic_snapshot_json,
                    verify_commands_json,
                    git_status_before,
                    git_status_after,
                    report_markdown,
                    report_json,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repair_runs
            """
            if clauses:
                query += f" WHERE {' AND '.join(clauses)}"
            query += " ORDER BY updated_at DESC, id DESC"
            if limit is not None and limit > 0:
                query += " LIMIT ?"
                params.append(limit)

            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_repair_run(self, run_id: str, **fields: Any) -> None:
        await self.init()
        if not fields:
            return
        normalized_fields = dict(fields)
        if any(key in normalized_fields for key in ("status", "report_markdown", "report_json", "last_error")):
            normalized_fields.setdefault("updated_at", _now_iso())
        else:
            normalized_fields["updated_at"] = _now_iso()
        if normalized_fields.get("status") in {"completed", "needs_review", "failed", "aborted"}:
            normalized_fields.setdefault("completed_at", _now_iso())

        clauses = ", ".join(f"{column} = ?" for column in normalized_fields)
        values = list(normalized_fields.values())
        values.append(run_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE repair_runs
                SET {clauses}
                WHERE run_id = ?
                """,
                tuple(values),
            )
            await db.commit()

    async def append_repair_run_event(
        self,
        *,
        run_id: str,
        event_type: str,
        source: str,
        summary: str = "",
        payload_json: str = "{}",
    ) -> None:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO repair_run_events(
                    run_id,
                    event_type,
                    source,
                    summary,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, event_type, source, summary, payload_json, _now_iso()),
            )
            await db.commit()

    async def list_repair_run_events(
        self,
        run_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT
                    id,
                    run_id,
                    event_type,
                    source,
                    summary,
                    payload_json,
                    created_at
                FROM repair_run_events
                WHERE run_id = ?
                ORDER BY created_at DESC, id DESC
            """
            params: list[Any] = [run_id]
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
                """
                DELETE FROM repair_run_events
                WHERE run_id IN (
                    SELECT run_id FROM repair_runs WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            await db.execute(
                "DELETE FROM repair_runs WHERE session_id = ?",
                (session_id,),
            )
            await db.execute(
                "DELETE FROM tool_invocations WHERE session_id = ?",
                (session_id,),
            )
            await db.execute(
                "DELETE FROM token_usage WHERE session_id = ?",
                (session_id,),
            )
            await db.execute(
                "DELETE FROM coder_tasks WHERE session_id = ?",
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
        session_id: str = "main",
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
                    session_id,
                    created_at,
                    updated_at,
                    next_run_at,
                    heartbeat_config
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    schedule_type,
                    schedule_value,
                    channel,
                    status,
                    session_id,
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
                    session_id,
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
                    session_id,
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
