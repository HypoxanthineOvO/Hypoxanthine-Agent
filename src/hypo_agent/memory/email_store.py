from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any

from hypo_agent.core.config_loader import get_memory_dir

_EMAIL_STORE_LOAD_ERRORS = (OSError, json.JSONDecodeError, TypeError, ValueError)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EmailStore:
    def __init__(
        self,
        *,
        root: Path | str | None = None,
        max_entries: int = 5000,
        retention_days: int = 90,
    ) -> None:
        self.root = Path(root or (get_memory_dir() / "emails"))
        self.index_path = self.root / "index.json"
        self.bodies_dir = self.root / "bodies"
        self.max_entries = max(1, int(max_entries))
        self.retention_days = max(1, int(retention_days))
        self._lock = Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.bodies_dir.mkdir(parents=True, exist_ok=True)

    def configure(self, *, max_entries: int | None = None, retention_days: int | None = None) -> None:
        if max_entries is not None:
            self.max_entries = max(1, int(max_entries))
        if retention_days is not None:
            self.retention_days = max(1, int(retention_days))

    def upsert(self, email_meta: dict[str, Any]) -> dict[str, Any]:
        message_id = str(email_meta.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("message_id is required")

        with self._lock:
            index = self._load_index_unlocked()
            existing = index.get(message_id, {})
            record = {
                "message_id": message_id,
                "subject": str(email_meta.get("subject") or existing.get("subject") or ""),
                "from": str(email_meta.get("from") or existing.get("from") or ""),
                "to": str(email_meta.get("to") or existing.get("to") or ""),
                "date": str(email_meta.get("date") or existing.get("date") or ""),
                "snippet": str(email_meta.get("snippet") or existing.get("snippet") or ""),
                "labels": self._normalize_labels(email_meta.get("labels") or existing.get("labels") or []),
                "cached_at": str(email_meta.get("cached_at") or _now_iso()),
                "has_body": bool(existing.get("has_body") or email_meta.get("has_body")),
            }
            for optional_key in ("account_name", "attachment_paths", "category"):
                value = email_meta.get(optional_key, existing.get(optional_key))
                if value is not None:
                    record[optional_key] = value
            index[message_id] = record
            cleaned_index, _ = self._cleanup_index_unlocked(index)
            self._save_index_unlocked(cleaned_index)
            persisted = cleaned_index.get(message_id, record)
            return dict(persisted)

    def upsert_body(self, message_id: str, body_text: str) -> None:
        key = str(message_id or "").strip()
        if not key:
            raise ValueError("message_id is required")

        with self._lock:
            payload = {
                "message_id": key,
                "body_text": str(body_text),
                "cached_at": _now_iso(),
            }
            self._body_path(key).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            index = self._load_index_unlocked()
            existing = index.get(key, {"message_id": key})
            existing["has_body"] = True
            existing["cached_at"] = payload["cached_at"]
            index[key] = existing
            cleaned_index, _ = self._cleanup_index_unlocked(index)
            self._save_index_unlocked(cleaned_index)

    def get_body(self, message_id: str) -> str | None:
        key = str(message_id or "").strip()
        if not key:
            return None
        path = self._body_path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except _EMAIL_STORE_LOAD_ERRORS:
            return None
        if not isinstance(payload, dict):
            return None
        body = payload.get("body_text")
        return str(body) if isinstance(body, str) else None

    def get_metadata(self, message_id: str) -> dict[str, Any] | None:
        key = str(message_id or "").strip()
        if not key:
            return None
        with self._lock:
            index = self._load_index_unlocked()
            record = index.get(key)
        return dict(record) if isinstance(record, dict) else None

    def search_local(self, query: str, *, limit: int = 20, hours_back: int = 24 * 7) -> list[dict[str, Any]]:
        terms = self._split_terms(query)
        if not terms:
            return []
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, int(hours_back)))
        with self._lock:
            index = self._load_index_unlocked()
        scored: list[tuple[float, int, float, dict[str, Any]]] = []
        for record in index.values():
            if not isinstance(record, dict):
                continue
            record_dt = self._record_datetime(record)
            if record_dt is not None and record_dt < cutoff:
                continue
            body = self.get_body(str(record.get("message_id") or "")) if bool(record.get("has_body")) else ""
            score, matched_terms = self._score_record(record, body_text=body or "", terms=terms)
            if matched_terms < len(terms):
                continue
            ts = record_dt.timestamp() if record_dt is not None else 0.0
            scored.append((score, matched_terms, ts, dict(record)))
        scored.sort(key=lambda item: (-item[0], -item[1], -item[2]))
        return [item[3] for item in scored[: max(1, int(limit))]]

    def list_recent(
        self,
        *,
        hours_back: int = 24,
        limit: int = 50,
        from_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, int(hours_back)))
        from_token = str(from_filter or "").strip().lower()
        with self._lock:
            index = self._load_index_unlocked()
        rows: list[tuple[float, dict[str, Any]]] = []
        for record in index.values():
            if not isinstance(record, dict):
                continue
            record_dt = self._record_datetime(record)
            if record_dt is not None and record_dt < cutoff:
                continue
            sender = str(record.get("from") or "")
            if from_token and from_token not in sender.lower():
                continue
            rows.append((record_dt.timestamp() if record_dt is not None else 0.0, dict(record)))
        rows.sort(key=lambda item: -item[0])
        return [item[1] for item in rows[: max(1, int(limit))]]

    def count_recent(self, *, hours_back: int = 24) -> int:
        return len(self.list_recent(hours_back=hours_back, limit=max(self.max_entries, 1)))

    def cleanup(self) -> dict[str, int]:
        with self._lock:
            index = self._load_index_unlocked()
            cleaned_index, stats = self._cleanup_index_unlocked(index)
            self._save_index_unlocked(cleaned_index)
        return stats

    def needs_warmup(self, *, max_age_hours: int = 24) -> bool:
        with self._lock:
            index = self._load_index_unlocked()
        if not index:
            return True
        latest = max((self._record_datetime(item) for item in index.values() if isinstance(item, dict)), default=None)
        if latest is None:
            return True
        return latest < (datetime.now(UTC) - timedelta(hours=max(1, int(max_age_hours))))

    def _split_terms(self, query: str) -> list[str]:
        return [item.strip().lower() for item in str(query or "").split() if item.strip()]

    def _score_record(
        self,
        record: dict[str, Any],
        *,
        body_text: str,
        terms: list[str],
    ) -> tuple[float, int]:
        subject = str(record.get("subject") or "").lower()
        sender = str(record.get("from") or "").lower()
        recipient = str(record.get("to") or "").lower()
        snippet = str(record.get("snippet") or "").lower()
        body = body_text.lower()
        score = 0.0
        matched_terms = 0
        for term in terms:
            term_score = 0.0
            if term in subject:
                term_score = max(term_score, 100.0)
            if term in sender or term in recipient:
                term_score = max(term_score, 80.0)
            if term in snippet:
                term_score = max(term_score, 60.0)
            if body and term in body:
                term_score = max(term_score, 40.0)
            if term_score <= 0:
                return 0.0, matched_terms
            matched_terms += 1
            score += term_score

        record_dt = self._record_datetime(record)
        if record_dt is not None:
            age_hours = max(0.0, (datetime.now(UTC) - record_dt).total_seconds() / 3600.0)
            score += max(0.0, 48.0 - age_hours)
        return score, matched_terms

    def _load_index_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except _EMAIL_STORE_LOAD_ERRORS:
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            message_id = str(value.get("message_id") or key or "").strip()
            if not message_id:
                continue
            normalized[message_id] = dict(value)
        return normalized

    def _save_index_unlocked(self, index: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _cleanup_index_unlocked(
        self,
        index: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=self.retention_days)
        kept: list[tuple[datetime, str, dict[str, Any]]] = []
        removed_ids: list[str] = []
        removed_expired = 0
        for message_id, record in index.items():
            record_dt = self._record_datetime(record) or now
            if record_dt < cutoff:
                removed_ids.append(message_id)
                removed_expired += 1
                continue
            kept.append((record_dt, message_id, record))
        kept.sort(key=lambda item: item[0], reverse=True)
        overflow = max(0, len(kept) - self.max_entries)
        overflow_ids = [item[1] for item in kept[self.max_entries :]] if overflow > 0 else []
        removed_ids.extend(overflow_ids)
        cleaned_index = {
            item[1]: item[2]
            for item in kept[: self.max_entries]
        }
        for message_id in removed_ids:
            body_path = self._body_path(message_id)
            if body_path.exists():
                try:
                    body_path.unlink()
                except OSError:
                    pass
        return cleaned_index, {
            "removed_expired": removed_expired,
            "removed_overflow": overflow,
        }

    def _normalize_labels(self, labels: Any) -> list[str]:
        if not isinstance(labels, list):
            return []
        seen: list[str] = []
        for item in labels:
            label = str(item or "").strip()
            if label and label not in seen:
                seen.append(label)
        return seen

    def _record_datetime(self, record: dict[str, Any]) -> datetime | None:
        for key in ("date", "cached_at"):
            value = str(record.get(key) or "").strip()
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    def _body_path(self, message_id: str) -> Path:
        digest = hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
        return self.bodies_dir / f"{digest}.json"
