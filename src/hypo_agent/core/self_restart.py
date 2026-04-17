from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from time import time
from typing import Any, Awaitable, Callable

DEFAULT_RESTART_LOCK_PATH = Path("/tmp/hypo_agent_restart.lock")
RESTART_COOLDOWN_SECONDS = 600
RESTART_EXIT_CODE = 42


async def graceful_restart(
    reason: str,
    event_emitter: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    *,
    force: bool = False,
    cooldown_seconds: int = RESTART_COOLDOWN_SECONDS,
    lock_path: Path | str = DEFAULT_RESTART_LOCK_PATH,
    now_fn: Callable[[], float] = time,
    exit_fn: Callable[[int], None] = os._exit,
) -> str:
    resolved_reason = str(reason or "").strip() or "unspecified restart reason"
    resolved_lock_path = Path(lock_path)
    now = float(now_fn())
    existing = _read_restart_lock(resolved_lock_path)
    if not force and existing is not None:
        requested_at = float(existing.get("requested_at") or 0.0)
        if requested_at > 0 and now - requested_at < int(cooldown_seconds):
            remaining = int(cooldown_seconds - (now - requested_at))
            return f"重启冷却中，请在 {remaining}s 后重试。"

    payload = {
        "reason": resolved_reason,
        "requested_at": now,
        "force": bool(force),
    }
    resolved_lock_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    if event_emitter is not None:
        event = {
            "type": "system_restart",
            "reason": resolved_reason,
            "timestamp": now,
            "force": bool(force),
        }
        emitted = event_emitter(event)
        if inspect.isawaitable(emitted):
            await emitted

    exit_fn(RESTART_EXIT_CODE)
    return "正在执行有限自重启。"


def read_restart_lock(lock_path: Path | str = DEFAULT_RESTART_LOCK_PATH) -> dict[str, Any] | None:
    return _read_restart_lock(Path(lock_path))


def clear_restart_lock(lock_path: Path | str = DEFAULT_RESTART_LOCK_PATH) -> None:
    Path(lock_path).unlink(missing_ok=True)


def _read_restart_lock(lock_path: Path) -> dict[str, Any] | None:
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None
