from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

import structlog

from hypo_agent.models import CircuitBreakerConfig

logger = structlog.get_logger()


class CircuitBreaker:
    def __init__(
        self,
        config: CircuitBreakerConfig,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

        self._tool_failures: dict[str, int] = {}
        self._tool_blocked_until: dict[str, datetime] = {}
        self._session_failures: dict[str, int] = {}
        self._session_blocked_until: dict[str, datetime] = {}
        self._global_kill_switch = bool(config.global_kill_switch)

    def can_execute(self, tool_name: str, session_id: str | None) -> tuple[bool, str]:
        now = self._now_fn()

        if self._global_kill_switch:
            return False, "Global kill switch is enabled"

        if session_id:
            session_block_until = self._session_blocked_until.get(session_id)
            if session_block_until is not None:
                if now < session_block_until:
                    return False, f"session circuit breaker is open for '{session_id}'"
                self._session_blocked_until.pop(session_id, None)
                self._session_failures[session_id] = 0
                logger.info(
                    "circuit_breaker.session.recovered",
                    session_id=session_id,
                )

        tool_block_until = self._tool_blocked_until.get(tool_name)
        if tool_block_until is not None:
            if now < tool_block_until:
                return False, f"tool circuit breaker is open for '{tool_name}'"
            self._tool_blocked_until.pop(tool_name, None)
            self._tool_failures[tool_name] = 0
            logger.info("circuit_breaker.tool.recovered", tool_name=tool_name)

        return True, ""

    def record_success(self, tool_name: str, session_id: str | None) -> None:
        self._tool_failures[tool_name] = 0
        if session_id:
            self._session_blocked_until.pop(session_id, None)

    def record_failure(self, tool_name: str, session_id: str | None) -> None:
        now = self._now_fn()
        cooldown_deadline = now + timedelta(seconds=self.config.cooldown_seconds)

        next_tool_count = self._tool_failures.get(tool_name, 0) + 1
        self._tool_failures[tool_name] = next_tool_count
        if next_tool_count >= self.config.tool_level_max_failures:
            self._tool_blocked_until[tool_name] = cooldown_deadline
            self._tool_failures[tool_name] = 0
            logger.warning(
                "circuit_breaker.tool.open",
                tool_name=tool_name,
                max_failures=self.config.tool_level_max_failures,
            )

        if session_id is None:
            return

        next_session_count = self._session_failures.get(session_id, 0) + 1
        self._session_failures[session_id] = next_session_count
        if next_session_count >= self.config.session_level_max_failures:
            self._session_blocked_until[session_id] = cooldown_deadline
            self._session_failures[session_id] = 0
            logger.warning(
                "circuit_breaker.session.open",
                session_id=session_id,
                max_failures=self.config.session_level_max_failures,
            )

    def set_global_kill_switch(self, enabled: bool) -> None:
        self._global_kill_switch = bool(enabled)

    def get_global_kill_switch(self) -> bool:
        return self._global_kill_switch
