from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any, Literal
from uuid import uuid4

MemoryClass = Literal[
    "user_profile",
    "interaction_policy",
    "operational_state",
    "credentials_state",
    "knowledge_note",
    "sop",
]


def classify_legacy_memory_key(key: str, value: str) -> MemoryClass:
    del value
    normalized = str(key or "").strip().lower()
    if normalized.startswith("auth.") or ".auth." in normalized or "token" in normalized or "cookie" in normalized:
        return "credentials_state"
    if (
        normalized.endswith(".cursor")
        or "cursor" in normalized
        or normalized.startswith("email_scan.")
        or normalized.startswith("notion.todo_")
        or normalized.endswith("_id")
        or "channel" in normalized
    ):
        return "operational_state"
    if any(token in normalized for token in ("reply", "language", "tone", "format", "boundary", "style")):
        return "interaction_policy"
    if normalized.startswith("sop.") or normalized.startswith("procedure."):
        return "sop"
    if normalized.startswith("knowledge.") or normalized.startswith("note."):
        return "knowledge_note"
    return "user_profile"


class TypedMemoryMigrator:
    def __init__(self, structured_store: Any, *, backup_dir: Path | str) -> None:
        self.structured_store = structured_store
        self.backup_dir = Path(backup_dir).expanduser().resolve(strict=False)

    async def backup(self, *, reason: str = "typed memory migration") -> dict[str, Any]:
        await self.structured_store.init()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_id = f"memory-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        target_dir = self.backup_dir / backup_id
        target_dir.mkdir(parents=True, exist_ok=True)
        db_path = Path(self.structured_store.db_path)
        db_backup_path = target_dir / db_path.name
        shutil.copy2(db_path, db_backup_path)
        manifest = {
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "database_path": str(db_path),
            "database_backup_path": str(db_backup_path),
        }
        manifest_path = target_dir / "manifest.json"
        manifest["manifest_path"] = str(manifest_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    async def migrate_legacy_preferences(self) -> dict[str, Any]:
        await self.structured_store.init()
        rows = self.structured_store.list_preferences_sync(limit=10000)
        migrated = 0
        for key, value in rows:
            memory_class = classify_legacy_memory_key(key, value)
            await self.structured_store.save_memory_item(
                memory_class=memory_class,
                key=key,
                value=value,
                source="legacy_preferences",
                language=_detect_language(value),
                confidence=0.75,
                metadata_json=json.dumps(
                    {
                        "legacy_table": "preferences",
                        "legacy_key": key,
                        "classifier": "rule_based_v1",
                    },
                    ensure_ascii=False,
                ),
                rollback_metadata_json=json.dumps({"restore_from": "backup_manifest"}, ensure_ascii=False),
            )
            migrated += 1
        return {"migrated": migrated}

    async def rollback(self, manifest_path: str | Path) -> None:
        manifest_file = Path(manifest_path)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        backup_path = Path(str(manifest["database_backup_path"]))
        target_path = Path(str(manifest["database_path"]))
        shutil.copy2(backup_path, target_path)
        # The file has been replaced under the same path; force future callers to re-check schema state.
        if hasattr(self.structured_store, "_initialized"):
            self.structured_store._initialized = False


def _detect_language(value: str) -> str:
    text = str(value or "")
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    return "en"
