from __future__ import annotations

from collections import deque
from pathlib import Path
from urllib.parse import quote

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.models import Message


class SessionMemory:
    def __init__(
        self,
        sessions_dir: Path | str | None = None,
        buffer_limit: int = 20,
    ) -> None:
        if buffer_limit <= 0:
            raise ValueError("buffer_limit must be greater than 0")

        if sessions_dir is None:
            sessions_dir = get_memory_dir() / "sessions"

        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_limit = buffer_limit
        self._buffers: dict[str, deque[Message]] = {}
        self._loaded: set[str] = set()

    def append(self, message: Message) -> None:
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

        messages = list(self._buffers.get(session_id, []))
        if limit is None:
            return messages
        return messages[-limit:]

    def get_messages(self, session_id: str) -> list[Message]:
        # TODO(M9+): add pagination/streaming for very large session histories.
        return self._read_all_messages(session_id)

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
                    "created_at": first.timestamp.isoformat(),
                    "updated_at": last.timestamp.isoformat(),
                    "message_count": len(messages),
                }
            )

        sessions.sort(
            key=lambda item: (
                str(item["updated_at"]),
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
                buffer.append(Message.model_validate_json(stripped))

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
            messages.append(Message.model_validate_json(stripped))
        return messages

    def _session_file(self, session_id: str) -> Path:
        # Encode session IDs to avoid path traversal and unsafe file names.
        safe_session = quote(session_id, safe="")
        return self.sessions_dir / f"{safe_session}.jsonl"
