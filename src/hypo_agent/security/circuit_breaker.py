from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

import structlog

from hypo_agent.models import CircuitBreakerConfig

logger = structlog.get_logger("hypo_agent.security.circuit_breaker")


class CircuitBreaker:
    def __init__(
        self,
        config: CircuitBreakerConfig,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

        self._tool_failures: dict[tuple[str | None, str], int] = {}
        self._tool_fused: set[tuple[str | None, str]] = set()
        self._skill_failures: dict[tuple[str | None, str, str], int] = {}
        self._skill_fused: set[tuple[str | None, str, str]] = set()
        self._session_failures: dict[str, int] = {}
        self._session_blocked_until: dict[str, datetime] = {}
        self._global_kill_switch = bool(config.global_kill_switch)

    def can_execute(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str | None = None,
    ) -> tuple[bool, str]:
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

        tool_key = (session_id, tool_name)
        if tool_key in self._tool_fused:
            return (
                False,
                (
                    "Tool '{tool_name}' has been disabled after "
                    f"{self.config.tool_level_max_failures} consecutive failures this session."
                ).format(tool_name=tool_name),
            )

        normalized_skill = self._normalize_skill_name(skill_name)
        if self.config.skill_level_enabled and normalized_skill:
            skill_key = (session_id, tool_name, normalized_skill)
            if skill_key in self._skill_fused:
                return (
                    False,
                    (
                        "Tool '{tool_name}' for logical skill '{skill_name}' has been disabled after "
                        f"{self._skill_failure_threshold()} consecutive failures this session."
                    ).format(tool_name=tool_name, skill_name=normalized_skill),
                )

        return True, ""

    def record_success(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str | None = None,
    ) -> None:
        tool_key = (session_id, tool_name)
        self._tool_failures[tool_key] = 0
        normalized_skill = self._normalize_skill_name(skill_name)
        if normalized_skill:
            self._skill_failures[(session_id, tool_name, normalized_skill)] = 0
        if session_id:
            self._session_blocked_until.pop(session_id, None)

    def record_failure(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str | None = None,
    ) -> None:
        now = self._now_fn()
        cooldown_deadline = now + timedelta(seconds=self.config.cooldown_seconds)

        tool_key = (session_id, tool_name)
        next_tool_count = self._tool_failures.get(tool_key, 0) + 1
        self._tool_failures[tool_key] = next_tool_count
        if next_tool_count >= self.config.tool_level_max_failures:
            self._tool_fused.add(tool_key)
            self._tool_failures[tool_key] = 0
            logger.warning(
                "circuit_breaker.tool_fused",
                tool_name=tool_name,
                session_id=session_id,
                max_failures=self.config.tool_level_max_failures,
            )

        normalized_skill = self._normalize_skill_name(skill_name)
        if self.config.skill_level_enabled and normalized_skill:
            skill_key = (session_id, tool_name, normalized_skill)
            next_skill_count = self._skill_failures.get(skill_key, 0) + 1
            self._skill_failures[skill_key] = next_skill_count
            threshold = self._skill_failure_threshold()
            if next_skill_count >= threshold:
                self._skill_fused.add(skill_key)
                self._skill_failures[skill_key] = 0
                logger.warning(
                    "circuit_breaker.skill_fused",
                    tool_name=tool_name,
                    skill_name=normalized_skill,
                    session_id=session_id,
                    max_failures=threshold,
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

    def _normalize_skill_name(self, skill_name: str | None) -> str:
        return str(skill_name or "").strip()

    def _skill_failure_threshold(self) -> int:
        configured = self.config.skill_level_max_failures
        if configured is None:
            return self.config.tool_level_max_failures
        return max(1, int(configured))
