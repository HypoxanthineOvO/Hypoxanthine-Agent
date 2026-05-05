"""Image generation history storage (C2 M4).

Stores generation records as JSONL for easy append and query.
Each record contains session_id, prompt, tool, status, output_paths,
duration_ms, error_info, and timestamp.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ImageGenHistory:
    """Append-only JSONL store for image generation history."""

    def __init__(self, *, store_path: Path | str) -> None:
        self._store_path = Path(store_path)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        # Touch file if it doesn't exist
        if not self._store_path.exists():
            self._store_path.touch()

    def record(
        self,
        *,
        session_id: str,
        prompt: str,
        tool: str,
        status: str,
        output_paths: list[str] | None = None,
        duration_ms: int | None = None,
        error_info: str | None = None,
        source_image: str | None = None,
        reference_url: str | None = None,
    ) -> dict[str, Any]:
        """Append a generation record and return it."""
        entry: dict[str, Any] = {
            "session_id": session_id,
            "prompt": prompt,
            "tool": tool,
            "status": status,
            "timestamp": _utc_now_iso(),
        }
        if output_paths:
            entry["output_paths"] = output_paths
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if error_info:
            entry["error_info"] = error_info
        if source_image:
            entry["source_image"] = source_image
        if reference_url:
            entry["reference_url"] = reference_url

        with self._store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return entry

    def query(
        self,
        *,
        session_id: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query records, optionally filtered by session_id or timestamp."""
        results: list[dict[str, Any]] = []
        with self._store_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if session_id and entry.get("session_id") != session_id:
                    continue
                if since and entry.get("timestamp", "") < since:
                    continue

                results.append(entry)

        return results

    def count(self) -> int:
        """Return the total number of records."""
        with self._store_path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
