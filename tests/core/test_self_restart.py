from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hypo_agent.core.self_restart import RESTART_EXIT_CODE, graceful_restart


def test_cooldown_blocks_restart(tmp_path: Path) -> None:
    async def _run() -> None:
        lock_path = tmp_path / "restart.lock"
        lock_path.write_text(json.dumps({"requested_at": 950.0, "reason": "old"}), encoding="utf-8")
        emitted: list[dict] = []
        exited: list[int] = []

        def exit_fn(code: int) -> None:
            exited.append(code)

        async def emit(event: dict) -> None:
            emitted.append(event)

        message = await graceful_restart(
            "retry",
            emit,
            lock_path=lock_path,
            now_fn=lambda: 1000.0,
            exit_fn=exit_fn,
        )

        assert "冷却" in message
        assert emitted == []
        assert exited == []

    asyncio.run(_run())


def test_cooldown_expired_allows(tmp_path: Path) -> None:
    async def _run() -> None:
        lock_path = tmp_path / "restart.lock"
        lock_path.write_text(json.dumps({"requested_at": 1.0, "reason": "old"}), encoding="utf-8")
        exited: list[int] = []

        def exit_fn(code: int) -> None:
            exited.append(code)

        message = await graceful_restart(
            "retry",
            None,
            lock_path=lock_path,
            now_fn=lambda: 1000.0,
            exit_fn=exit_fn,
        )

        assert "重启" in message
        assert exited == [RESTART_EXIT_CODE]

    asyncio.run(_run())


def test_lock_file_written(tmp_path: Path) -> None:
    async def _run() -> None:
        lock_path = tmp_path / "restart.lock"

        def exit_fn(code: int) -> None:
            del code

        await graceful_restart(
            "repair pipeline",
            None,
            lock_path=lock_path,
            now_fn=lambda: 1000.0,
            exit_fn=exit_fn,
        )

        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["reason"] == "repair pipeline"
        assert payload["requested_at"] == 1000.0

    asyncio.run(_run())


def test_event_emitter_called(tmp_path: Path) -> None:
    async def _run() -> None:
        lock_path = tmp_path / "restart.lock"
        emitted: list[dict] = []

        def exit_fn(code: int) -> None:
            del code

        async def emit(event: dict) -> None:
            emitted.append(event)

        await graceful_restart(
            "repair pipeline",
            emit,
            lock_path=lock_path,
            now_fn=lambda: 1000.0,
            exit_fn=exit_fn,
        )

        assert emitted == [
            {
                "type": "system_restart",
                "reason": "repair pipeline",
                "timestamp": 1000.0,
                "force": False,
            }
        ]

    asyncio.run(_run())
