from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class _StubRouter:
    def get_model_for_task(self, task_type: str) -> str:
        del task_type
        return "GeminiLow"


def test_pipeline_records_deduplicated_model_fallback_alert(tmp_path: Path) -> None:
    db_path = tmp_path / "hypo.db"
    store = StructuredStore(db_path=db_path)
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    pipeline = ChatPipeline(
        router=_StubRouter(),
        chat_model="GeminiLow",
        session_memory=session_memory,
        structured_store=store,
    )

    async def _run() -> None:
        for _ in range(4):
            await pipeline._emit_progress_event(
                {
                    "type": "model_fallback",
                    "provider": "VSPLab_Gemini",
                    "requested_model": "GeminiLow",
                    "failed_model": "GeminiLow",
                    "fallback_model": "GPT",
                    "reason": "Invalid request",
                    "session_id": "main",
                }
            )

    asyncio.run(_run())

    with sqlite3.connect(db_path) as conn:
        alert_rows = conn.execute(
            "SELECT category, signature, message FROM alerts ORDER BY id ASC"
        ).fetchall()

    assert len(alert_rows) == 1
    assert alert_rows[0][0] == "model_fallback"
    assert alert_rows[0][1] == "model_fallback:vsplab_gemini"
    assert "3 model fallbacks" in alert_rows[0][2]
