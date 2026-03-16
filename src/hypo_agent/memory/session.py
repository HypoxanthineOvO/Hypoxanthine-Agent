from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import quote

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.core.time_utils import normalize_utc_datetime, utc_isoformat, utc_now
from hypo_agent.models import Message

DEFAULT_SESSION_ID = "main"
LEGACY_SESSION_ID = "session-1"


class SessionMemory:
    def __init__(
        self,
        sessions_dir: Path | str | None = None,
        buffer_limit: int = 20,
        active_window_days: int | None = None,
        now_fn=None,
    ) -> None:
        if buffer_limit <= 0:
            raise ValueError("buffer_limit must be greater than 0")

        if sessions_dir is None:
            sessions_dir = get_memory_dir() / "sessions"

        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_main_session()
        self.buffer_limit = buffer_limit
        self.active_window_days = (
            None
            if active_window_days is None
            else max(0, int(active_window_days))
        )
        self._now_fn = now_fn or utc_now
        self._buffers: dict[str, deque[Message]] = {}
        self._loaded: set[str] = set()

    def append(self, message: Message) -> None:
        if message.timestamp is None:
            message = message.model_copy(update={"timestamp": utc_now()})
        self._ensure_loaded(message.session_id)
        self._append_to_buffer(message)

        session_file = self._session_file(message.session_id)
        with session_file.open("a", encoding="utf-8") as handle:
            handle.write(message.model_dump_json())
            handle.write("\n")

    def get_recent_messages(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[Message]:
        self._ensure_loaded(session_id)
        if limit is not None and limit <= 0:
            return []

        if not self._session_within_active_window(
            session_id,
            messages=list(self._buffers.get(session_id, [])),
        ):
            return []

        messages = list(self._buffers.get(session_id, []))
        if limit is None:
            return messages
        return messages[-limit:]

    def get_messages(self, session_id: str) -> list[Message]:
        # TODO(M9+): add pagination/streaming for very large session histories.
        messages = self._read_all_messages(session_id)
        if not self._session_within_active_window(session_id, messages=messages):
            return []
        return messages

    def clear_session(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)
        self._loaded.discard(session_id)
        self._session_file(session_id).unlink(missing_ok=True)

    def list_sessions(self) -> list[dict[str, object]]:
        # TODO(M9+): optimize to read only first/last non-empty lines per file.
        sessions: list[dict[str, object]] = []
        for session_file in self.sessions_dir.glob("*.jsonl"):
            lines = [
                line.strip()
                for line in session_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not lines:
                continue

            messages = [Message.model_validate_json(line) for line in lines]
            first = messages[0]
            last = messages[-1]
            sessions.append(
                {
                    "session_id": first.session_id,
                    "created_at": utc_isoformat(first.timestamp),
                    "updated_at": utc_isoformat(last.timestamp),
                    "message_count": len(messages),
                }
            )

        sessions.sort(
            key=lambda item: (
                str(item["updated_at"] or ""),
                str(item["session_id"]),
            ),
            reverse=True,
        )
        return sessions

    def _append_to_buffer(self, message: Message) -> None:
        if message.session_id not in self._buffers:
            self._buffers[message.session_id] = deque(maxlen=self.buffer_limit)
        self._buffers[message.session_id].append(message)

    def _ensure_loaded(self, session_id: str) -> None:
        if session_id in self._loaded:
            return

        buffer: deque[Message] = deque(maxlen=self.buffer_limit)
        session_file = self._session_file(session_id)
        if session_file.exists():
            for line in session_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                buffer.append(self._parse_session_message(stripped))

        self._buffers[session_id] = buffer
        self._loaded.add(session_id)

    def _read_all_messages(self, session_id: str) -> list[Message]:
        session_file = self._session_file(session_id)
        if not session_file.exists():
            return []

        messages: list[Message] = []
        for line in session_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            messages.append(self._parse_session_message(stripped))
        return messages

    def _migrate_legacy_main_session(self) -> None:
        main_file = self._session_file(DEFAULT_SESSION_ID)
        legacy_file = self._session_file(LEGACY_SESSION_ID)
        if main_file.exists() or not legacy_file.exists():
            return

        lines = legacy_file.read_text(encoding="utf-8").splitlines()
        migrated_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = self._parse_session_message(stripped)
            except Exception:
                main_file.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")
                return
            migrated_lines.append(
                message.model_copy(update={"session_id": DEFAULT_SESSION_ID}).model_dump_json()
            )

        content = "\n".join(migrated_lines)
        if content:
            content += "\n"
        main_file.write_text(content, encoding="utf-8")

    def _session_file(self, session_id: str) -> Path:
        # Encode session IDs to avoid path traversal and unsafe file names.
        safe_session = quote(session_id, safe="")
        return self.sessions_dir / f"{safe_session}.jsonl"

    def _parse_session_message(self, line: str) -> Message:
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("session message must be an object")
        if "timestamp" not in payload:
            payload["timestamp"] = None
        return Message.model_validate(payload)

    def _session_within_active_window(
        self,
        session_id: str,
        *,
        messages: list[Message],
    ) -> bool:
        if self.active_window_days is None:
            return True
        if self.active_window_days <= 0:
            return False

        last_activity = self._resolve_last_activity(session_id, messages=messages)
        if last_activity is None:
            return True
        cutoff = normalize_utc_datetime(self._now_fn())
        if cutoff is None:
            return True
        cutoff = cutoff - timedelta(days=self.active_window_days)
        return last_activity >= cutoff

    def _resolve_last_activity(
        self,
        session_id: str,
        *,
        messages: list[Message],
    ):
        latest = None
        for message in messages:
            normalized = normalize_utc_datetime(message.timestamp)
            if normalized is None:
                continue
            if latest is None or normalized > latest:
                latest = normalized
        if latest is not None:
            return latest

        session_file = self._session_file(session_id)
        if not session_file.exists():
            return None
        return normalize_utc_datetime(
            datetime.fromtimestamp(session_file.stat().st_mtime, tz=UTC)
        )
