from __future__ import annotations

from datetime import UTC, datetime, timedelta
import inspect
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import structlog

from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.memory.gc")


class MemoryGC:
    def __init__(
        self,
        *,
        session_memory: Any,
        structured_store: Any,
        semantic_memory: Any | None,
        model_router: Any,
        knowledge_dir: Path | str,
        sessions_dir: Path | str | None = None,
        active_window_days: int = 7,
        min_message_count: int = 5,
        now_fn=None,
    ) -> None:
        self.session_memory = session_memory
        self.structured_store = structured_store
        self.semantic_memory = semantic_memory
        self.model_router = model_router
        self.knowledge_dir = Path(knowledge_dir)
        self.sessions_dir = (
            Path(sessions_dir)
            if sessions_dir is not None
            else Path(getattr(session_memory, "sessions_dir"))
        )
        self.active_window_days = max(1, int(active_window_days))
        self.min_message_count = max(1, int(min_message_count))
        self._now_fn = now_fn or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.last_run_at: str | None = None
        self.last_result: dict[str, Any] | None = None

    async def run(self) -> dict[str, Any]:
        await self.structured_store.init()
        created_files: list[str] = []
        skipped_count = 0
        error_count = 0

        for session_file in sorted(self.sessions_dir.glob("*.jsonl")):
            try:
                processed = await self._process_session_file(session_file)
            except Exception:
                logger.exception("memory_gc.process_failed", file_path=str(session_file))
                error_count += 1
                continue

            if processed is None:
                skipped_count += 1
                continue
            created_files.append(processed)

        if created_files and self.semantic_memory is not None:
            rebuilder = getattr(self.semantic_memory, "build_index", None)
            if callable(rebuilder):
                result = rebuilder(self.knowledge_dir)
                if inspect.isawaitable(result):
                    await result

        summary = {
            "processed_count": len(created_files),
            "skipped_count": skipped_count,
            "error_count": error_count,
            "created_files": created_files,
            "last_run_at": self._now_iso(),
        }
        self.last_run_at = summary["last_run_at"]
        self.last_result = summary
        return summary

    def get_status(self) -> dict[str, Any]:
        return {
            "name": "memory_gc",
            "status": "idle",
            "last_run_at": self.last_run_at,
            "processed_count": int((self.last_result or {}).get("processed_count") or 0),
            "skipped_count": int((self.last_result or {}).get("skipped_count") or 0),
        }

    async def _process_session_file(self, session_file: Path) -> str | None:
        session_id = unquote(session_file.stem)
        if await self.structured_store.is_session_gc_processed(session_id):
            return None

        messages = self._read_session_messages(session_file)
        if len(messages) < self.min_message_count:
            return None

        last_active = self._last_activity_at(messages, session_file=session_file)
        if last_active is not None and last_active >= self._cutoff_time():
            return None

        summary_markdown = await self._summarize_session(session_id, messages)
        if not summary_markdown:
            return None

        output_path = self._summary_file_path(session_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(summary_markdown, encoding="utf-8")
        await self.structured_store.mark_session_gc_processed(session_id)

        updater = getattr(self.semantic_memory, "update_index", None)
        if callable(updater):
            result = updater(output_path)
            if inspect.isawaitable(result):
                await result
        return str(output_path)

    def _read_session_messages(self, session_file: Path) -> list[Message]:
        messages: list[Message] = []
        if not session_file.exists():
            return messages
        for raw_line in session_file.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                continue
            payload.setdefault("timestamp", None)
            messages.append(Message.model_validate(payload))
        return messages

    def _last_activity_at(self, messages: list[Message], *, session_file: Path) -> datetime | None:
        timestamps = [message.timestamp for message in messages if message.timestamp is not None]
        if timestamps:
            return max(item.astimezone(UTC).replace(microsecond=0) for item in timestamps)
        if not session_file.exists():
            return None
        return datetime.fromtimestamp(session_file.stat().st_mtime, tz=UTC).replace(microsecond=0)

    async def _summarize_session(self, session_id: str, messages: list[Message]) -> str:
        lightweight_model = self.model_router.get_model_for_task("lightweight")
        transcript = "\n".join(
            f"[{message.sender}] {str(message.text or '').strip()}"
            for message in messages
            if str(message.text or "").strip()
        )
        prompt = (
            "阅读以下会话记录，提取：踩坑记录、关键决策、用户偏好变更、重要知识点。"
            "如果没有有价值内容，只返回空字符串。输出必须是结构化 Markdown。\n\n"
            f"session_id={session_id}\n\n"
            f"{transcript}"
        )
        try:
            response = await self.model_router.call(
                lightweight_model,
                [{"role": "user", "content": prompt}],
                session_id=session_id,
            )
        except Exception:
            logger.exception("memory_gc.llm_summary_failed", session_id=session_id)
            return ""

        summary = str(response or "").strip()
        if not summary or summary.lower() in {"none", "null", "empty"}:
            return ""
        if summary in {"空", "无", "没有"}:
            return ""
        if not summary.startswith("#"):
            summary = f"# 会话摘要\n\n{summary}"
        return summary.rstrip() + "\n"

    def _summary_file_path(self, session_id: str) -> Path:
        safe_session_id = "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in session_id
        ).strip("-") or "session"
        date_prefix = self._now_fn().astimezone(UTC).date().isoformat()
        return self.knowledge_dir / "gc_summaries" / f"{date_prefix}_{safe_session_id}.md"

    def _cutoff_time(self) -> datetime:
        return self._now_fn().astimezone(UTC).replace(microsecond=0) - timedelta(
            days=self.active_window_days
        )

    def _now_iso(self) -> str:
        return self._now_fn().astimezone(UTC).replace(microsecond=0).isoformat()
