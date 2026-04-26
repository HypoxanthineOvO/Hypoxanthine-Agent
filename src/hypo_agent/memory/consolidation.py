from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import unquote
from uuid import uuid4

import structlog

from hypo_agent.memory.typed_memory import TypedMemoryMigrator, classify_legacy_memory_key
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.memory.consolidation")

_MEMORY_CLASSES = {
    "user_profile",
    "interaction_policy",
    "operational_state",
    "credentials_state",
    "knowledge_note",
    "sop",
}
_EXPLICIT_MEMORY_RE = re.compile(
    r"(?:记忆|memory)\s*[:：]\s*"
    r"(?P<memory_class>[a-z_]+)\.(?P<key>[A-Za-z0-9_.:-]+)\s*=\s*(?P<value>.+)",
    re.IGNORECASE,
)
_ARCHIVE_MEMORY_RE = re.compile(
    r"(?:记忆归档|archive memory|memory archive)\s*[:：]\s*"
    r"(?P<memory_class>[a-z_]+)\.(?P<key>[A-Za-z0-9_.:-]+)\s*=\s*(?P<value>.+)",
    re.IGNORECASE,
)
_RECOVERABLE_ERRORS = (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError)


@dataclass(frozen=True)
class MemoryCandidate:
    memory_class: str
    key: str
    value: str
    source: str
    language: str = "zh"
    confidence: float | None = 0.75
    reason: str = "candidate_extracted"
    operation: Literal["upsert", "archive"] = "upsert"
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryConsolidationService:
    def __init__(
        self,
        *,
        session_memory: Any,
        structured_store: Any,
        knowledge_dir: Path | str,
        sessions_dir: Path | str | None = None,
        backup_dir: Path | str | None = None,
        active_window_days: int = 7,
        min_message_count: int = 5,
        now_fn=None,
    ) -> None:
        self.session_memory = session_memory
        self.structured_store = structured_store
        self.knowledge_dir = Path(knowledge_dir)
        self.sessions_dir = (
            Path(sessions_dir)
            if sessions_dir is not None
            else Path(getattr(session_memory, "sessions_dir"))
        )
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir is not None
            else self.knowledge_dir / "backups" / "memory_consolidation"
        )
        self.active_window_days = max(1, int(active_window_days))
        self.min_message_count = max(1, int(min_message_count))
        self._now_fn = now_fn or (lambda: datetime.now(UTC).replace(microsecond=0))

    async def run(self, *, apply: bool = True) -> dict[str, Any]:
        await self.structured_store.init()
        report_id = self._report_id()
        report_file = self._report_file(report_id)
        candidates = await self.extract_candidates()
        items = await self._plan(candidates)
        apply_items = [
            item
            for item in items
            if item["action"] in {"added", "updated", "archived"}
        ]
        backup_manifest = None
        if apply and apply_items:
            migrator = TypedMemoryMigrator(self.structured_store, backup_dir=self.backup_dir)
            backup_manifest = await migrator.backup(reason="memory consolidation")
            await self._apply_items(apply_items, backup_manifest=backup_manifest, report_file=report_file)

        report = {
            "report_id": report_id,
            "created_at": self._now_iso(),
            "applied": bool(apply),
            "backup_manifest": backup_manifest,
            "counts": self._count_items(items),
            "items": items,
            "report_file": str(report_file),
        }
        self._write_report(report_file, report)
        return report

    async def rollback(self, report_file: str | Path) -> None:
        report = json.loads(Path(report_file).read_text(encoding="utf-8"))
        manifest = report.get("backup_manifest")
        if not isinstance(manifest, dict) or not manifest.get("manifest_path"):
            raise ValueError("report does not contain a rollback manifest")
        migrator = TypedMemoryMigrator(self.structured_store, backup_dir=self.backup_dir)
        await migrator.rollback(manifest["manifest_path"])

    async def extract_candidates(self) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        candidates.extend(await self._extract_session_candidates())
        candidates.extend(self._extract_legacy_preference_candidates())
        candidates.extend(self._extract_semantic_note_candidates())
        return candidates

    async def _extract_session_candidates(self) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for session_file in sorted(self.sessions_dir.glob("*.jsonl")):
            try:
                session_id = unquote(session_file.stem)
                if await self.structured_store.is_session_gc_processed(session_id):
                    continue
                messages = self._read_session_messages(session_file)
                if len(messages) < self.min_message_count:
                    continue
                last_active = self._last_activity_at(messages, session_file=session_file)
                if last_active is not None and last_active >= self._cutoff_time():
                    continue
                for message in messages:
                    candidates.extend(
                        self._extract_explicit_candidates(
                            str(message.text or ""),
                            source=f"session:{session_id}",
                            reason="session_candidate_extracted",
                            metadata={"session_id": session_id},
                        )
                    )
            except _RECOVERABLE_ERRORS:
                logger.exception("memory_consolidation.session_extract_failed", file_path=str(session_file))
        return candidates

    def _extract_legacy_preference_candidates(self) -> list[MemoryCandidate]:
        rows = self.structured_store.list_preferences_sync(limit=10000)
        candidates: list[MemoryCandidate] = []
        for key, value in rows:
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if not normalized_key or not normalized_value:
                continue
            memory_class = classify_legacy_memory_key(normalized_key, normalized_value)
            candidates.append(
                MemoryCandidate(
                    memory_class=memory_class,
                    key=normalized_key,
                    value=normalized_value,
                    source="legacy_preferences",
                    language=_detect_language(normalized_value),
                    confidence=0.75,
                    reason="legacy_preference_imported",
                    metadata={"legacy_table": "preferences", "legacy_key": normalized_key},
                )
            )
        return candidates

    def _extract_semantic_note_candidates(self) -> list[MemoryCandidate]:
        if not self.knowledge_dir.exists():
            return []
        candidates: list[MemoryCandidate] = []
        skipped_parts = {"gc_summaries", "consolidation_reports", "backups"}
        for note_file in sorted(self.knowledge_dir.rglob("*.md")):
            if skipped_parts.intersection(note_file.relative_to(self.knowledge_dir).parts):
                continue
            try:
                text = note_file.read_text(encoding="utf-8")
            except OSError:
                logger.exception("memory_consolidation.semantic_note_read_failed", file_path=str(note_file))
                continue
            candidates.extend(
                self._extract_explicit_candidates(
                    text,
                    source=f"semantic_note:{note_file.relative_to(self.knowledge_dir).as_posix()}",
                    reason="semantic_note_candidate_extracted",
                    metadata={"note_path": str(note_file)},
                )
            )
        return candidates

    async def _plan(self, candidates: list[MemoryCandidate]) -> list[dict[str, Any]]:
        existing_rows = await self.structured_store.list_memory_items(status="active")
        existing = {
            (str(row["memory_class"]), str(row["key"])): row
            for row in existing_rows
        }
        seen: dict[tuple[str, str], MemoryCandidate] = {}
        items: list[dict[str, Any]] = []

        for candidate in candidates:
            identity = (candidate.memory_class, candidate.key)
            if identity in seen:
                previous = seen[identity]
                if _same_memory_value(previous.value, candidate.value):
                    items.append(self._item(candidate, action="skipped", reason="duplicate_candidate"))
                else:
                    items.append(self._item(candidate, action="conflict", reason="candidate_value_conflict"))
                continue

            existing_row = existing.get(identity)
            if candidate.operation == "archive":
                if existing_row is None:
                    items.append(self._item(candidate, action="skipped", reason="archive_target_missing"))
                else:
                    items.append(
                        self._item(
                            candidate,
                            action="archived",
                            reason="archive_candidate_applied",
                            existing=existing_row,
                        )
                    )
                seen[identity] = candidate
                continue

            if existing_row is None:
                items.append(self._item(candidate, action="added", reason=candidate.reason))
                seen[identity] = candidate
                continue

            existing_value = str(existing_row.get("value") or "")
            if _same_memory_value(existing_value, candidate.value):
                items.append(self._item(candidate, action="skipped", reason="existing_duplicate"))
            elif str(existing_row.get("source") or "").startswith("memory_consolidation"):
                items.append(
                    self._item(
                        candidate,
                        action="updated",
                        reason="consolidated_memory_updated",
                        existing=existing_row,
                    )
                )
            else:
                items.append(
                    self._item(
                        candidate,
                        action="conflict",
                        reason="existing_value_conflict",
                        existing=existing_row,
                    )
                )
            seen[identity] = candidate
        return items

    async def _apply_items(
        self,
        items: list[dict[str, Any]],
        *,
        backup_manifest: dict[str, Any],
        report_file: Path,
    ) -> None:
        for item in items:
            status = "archived" if item["action"] == "archived" else "active"
            value = str(item.get("value") or "")
            if item["action"] == "archived":
                value = str((item.get("existing") or {}).get("value") or value)
            metadata = {
                "consolidation_report": str(report_file),
                "consolidation_reason": item["reason"],
                "candidate_metadata": item.get("metadata") or {},
            }
            rollback_metadata = {
                "backup_manifest": backup_manifest.get("manifest_path"),
                "report_file": str(report_file),
            }
            await self.structured_store.save_memory_item(
                memory_class=str(item["memory_class"]),
                key=str(item["key"]),
                value=value,
                source=f"memory_consolidation:{item['source']}",
                language=str(item.get("language") or "zh"),
                confidence=item.get("confidence"),
                status=status,
                metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                rollback_metadata_json=json.dumps(
                    rollback_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )

    def _extract_explicit_candidates(
        self,
        text: str,
        *,
        source: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for match in _ARCHIVE_MEMORY_RE.finditer(text):
            memory_class = str(match.group("memory_class") or "").strip()
            key = str(match.group("key") or "").strip()
            value = str(match.group("value") or "").strip()
            if memory_class not in _MEMORY_CLASSES or not key or not value:
                continue
            candidates.append(
                MemoryCandidate(
                    memory_class=memory_class,
                    key=key,
                    value=value,
                    source=source,
                    language=_detect_language(value),
                    confidence=0.8,
                    reason="archive_candidate_extracted",
                    operation="archive",
                    metadata=metadata,
                )
            )
        for match in _EXPLICIT_MEMORY_RE.finditer(text):
            memory_class = str(match.group("memory_class") or "").strip()
            key = str(match.group("key") or "").strip()
            value = str(match.group("value") or "").strip()
            if memory_class not in _MEMORY_CLASSES or not key or not value:
                continue
            candidates.append(
                MemoryCandidate(
                    memory_class=memory_class,
                    key=key,
                    value=value,
                    source=source,
                    language=_detect_language(value),
                    confidence=0.8,
                    reason=reason,
                    metadata=metadata,
                )
            )
        return candidates

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

    def _cutoff_time(self) -> datetime:
        return self._now_fn().astimezone(UTC).replace(microsecond=0) - timedelta(
            days=self.active_window_days
        )

    def _item(
        self,
        candidate: MemoryCandidate,
        *,
        action: str,
        reason: str,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "memory_class": candidate.memory_class,
            "key": candidate.key,
            "value": candidate.value,
            "language": candidate.language,
            "source": candidate.source,
            "confidence": candidate.confidence,
            "action": action,
            "reason": reason,
            "metadata": candidate.metadata,
        }
        if existing is not None:
            payload["existing"] = {
                "memory_id": existing.get("memory_id"),
                "source": existing.get("source"),
                "status": existing.get("status"),
                "value": existing.get("value"),
            }
        return payload

    def _count_items(self, items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "candidates": len(items),
            "added": sum(1 for item in items if item["action"] == "added"),
            "updated": sum(1 for item in items if item["action"] == "updated"),
            "archived": sum(1 for item in items if item["action"] == "archived"),
            "skipped": sum(1 for item in items if item["action"] == "skipped"),
            "conflicts": sum(1 for item in items if item["action"] == "conflict"),
        }

    def _write_report(self, report_file: Path, report: dict[str, Any]) -> None:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _report_file(self, report_id: str) -> Path:
        return self.knowledge_dir / "consolidation_reports" / f"{report_id}.json"

    def _report_id(self) -> str:
        return f"memory-consolidation-{self._now_fn().strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"

    def _now_iso(self) -> str:
        return self._now_fn().astimezone(UTC).replace(microsecond=0).isoformat()


def _same_memory_value(left: str, right: str) -> bool:
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def _detect_language(value: str) -> str:
    text = str(value or "")
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    return "en"
