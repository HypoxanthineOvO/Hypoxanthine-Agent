from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable

import structlog

logger = structlog.get_logger("hypo_agent.core.watchdog")


class AgentWatchdog:
    def __init__(
        self,
        *,
        pipeline: Any,
        heartbeat_service: Any | None = None,
        check_interval_seconds: float = 30.0,
        inactivity_timeout_seconds: float = 0.0,
        max_consecutive_heartbeat_failures: int = 3,
        exit_fn: Callable[[int], Any] = sys.exit,
    ) -> None:
        self.pipeline = pipeline
        self.heartbeat_service = heartbeat_service
        self.check_interval_seconds = max(1.0, float(check_interval_seconds))
        self.inactivity_timeout_seconds = max(0.0, float(inactivity_timeout_seconds))
        self.max_consecutive_heartbeat_failures = max(1, int(max_consecutive_heartbeat_failures))
        self.exit_fn = exit_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._exit_requested = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._exit_requested = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.check_interval_seconds,
                )
            except asyncio.TimeoutError:
                self._check_once()

    def _check_once(self) -> None:
        if self._exit_requested:
            return

        heartbeat_failures = int(
            getattr(self.heartbeat_service, "consecutive_failures", 0) or 0
        )
        if heartbeat_failures >= self.max_consecutive_heartbeat_failures:
            self._request_exit(
                reason="heartbeat_failures_exhausted",
                heartbeat_failures=heartbeat_failures,
            )
            return

        if self.inactivity_timeout_seconds <= 0 or not self._pipeline_consumer_running():
            return

        activity_age = self._pipeline_activity_age_seconds()
        if activity_age is not None and activity_age >= self.inactivity_timeout_seconds:
            self._request_exit(
                reason="pipeline_inactive_too_long",
                inactivity_seconds=round(activity_age, 3),
                last_activity_at=getattr(self.pipeline, "get_last_activity_at", lambda: "")(),
            )

    def _pipeline_consumer_running(self) -> bool:
        task = getattr(self.pipeline, "_event_consumer_task", None)
        return bool(task is not None and not task.done())

    def _pipeline_activity_age_seconds(self) -> float | None:
        getter = getattr(self.pipeline, "last_activity_age_seconds", None)
        if callable(getter):
            value = getter()
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return None

    def _request_exit(self, *, reason: str, **context: Any) -> None:
        self._exit_requested = True
        logger.error("agent.watchdog.exit_requested", reason=reason, **context)
        self.exit_fn(1)
