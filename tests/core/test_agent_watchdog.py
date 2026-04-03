from __future__ import annotations

import asyncio

from hypo_agent.core.agent_watchdog import AgentWatchdog


class RunningPipeline:
    def __init__(self, *, activity_age_seconds: float) -> None:
        loop = asyncio.new_event_loop()
        self._event_consumer_task = loop.create_future()
        self._last_activity_at = "2026-04-03T09:00:00+00:00"
        self._activity_age_seconds = activity_age_seconds

    def last_activity_age_seconds(self) -> float:
        return self._activity_age_seconds

    def get_last_activity_at(self) -> str:
        return self._last_activity_at


class HeartbeatStub:
    def __init__(self, consecutive_failures: int) -> None:
        self.consecutive_failures = consecutive_failures


def test_agent_watchdog_requests_exit_after_repeated_heartbeat_failures() -> None:
    exits: list[int] = []
    watchdog = AgentWatchdog(
        pipeline=RunningPipeline(activity_age_seconds=1.0),
        heartbeat_service=HeartbeatStub(consecutive_failures=3),
        max_consecutive_heartbeat_failures=3,
        inactivity_timeout_seconds=900.0,
        exit_fn=exits.append,
    )

    watchdog._check_once()

    assert exits == [1]


def test_agent_watchdog_requests_exit_after_pipeline_inactivity() -> None:
    exits: list[int] = []
    watchdog = AgentWatchdog(
        pipeline=RunningPipeline(activity_age_seconds=901.0),
        heartbeat_service=HeartbeatStub(consecutive_failures=0),
        max_consecutive_heartbeat_failures=3,
        inactivity_timeout_seconds=900.0,
        exit_fn=exits.append,
    )

    watchdog._check_once()

    assert exits == [1]
