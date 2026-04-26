from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypo_agent.models import CircuitBreakerConfig
from hypo_agent.security.circuit_breaker import CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _config() -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        tool_level_max_failures=3,
        session_level_max_failures=5,
        cooldown_seconds=10,
        global_kill_switch=False,
    )


def test_tool_level_breaker_fuses_tool_for_session() -> None:
    breaker = CircuitBreaker(_config())

    breaker.record_failure(tool_name="exec_command", session_id="s1")
    breaker.record_failure(tool_name="exec_command", session_id="s1")
    breaker.record_failure(tool_name="exec_command", session_id="s1")

    allowed, reason = breaker.can_execute("exec_command", "s1")
    assert allowed is False
    assert "disabled" in reason.lower()

    allowed_other, _ = breaker.can_execute("exec_command", "s2")
    assert allowed_other is True


def test_zero_weight_outcomes_do_not_increment_breaker_counts() -> None:
    breaker = CircuitBreaker(_config())

    for _ in range(10):
        breaker.record_outcome(
            tool_name="read_file",
            session_id="s1",
            skill_name="filesystem",
            breaker_weight=0,
        )

    allowed, reason = breaker.can_execute("read_file", "s1", "filesystem")
    assert allowed is True
    assert reason == ""


def test_weighted_outcomes_fuse_only_when_threshold_reached() -> None:
    breaker = CircuitBreaker(_config())

    breaker.record_outcome(
        tool_name="exec_command",
        session_id="s1",
        skill_name="exec",
        breaker_weight=2,
    )
    allowed_before, reason_before = breaker.can_execute("exec_command", "s1", "exec")
    assert allowed_before is True
    assert reason_before == ""

    breaker.record_outcome(
        tool_name="exec_command",
        session_id="s1",
        skill_name="exec",
        breaker_weight=1,
    )
    allowed_after, reason_after = breaker.can_execute("exec_command", "s1", "exec")
    assert allowed_after is False
    assert "disabled" in reason_after.lower()


def test_session_failure_count_resets_on_success() -> None:
    breaker = CircuitBreaker(_config())

    for index in range(5):
        breaker.record_failure(tool_name=f"tool_{index}", session_id="s1")
        breaker.record_success(tool_name=f"tool_{index}", session_id="s1")

    allowed, reason = breaker.can_execute("another_tool", "s1")

    assert allowed is True
    assert reason == ""


def test_tool_level_breaker_recovers_after_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(_config(), now_fn=clock.now)

    breaker.record_failure(tool_name="exec_command", session_id="s1")
    breaker.record_failure(tool_name="exec_command", session_id="s1")
    breaker.record_failure(tool_name="exec_command", session_id="s1")

    allowed_before, reason_before = breaker.can_execute("exec_command", "s1")
    assert allowed_before is False
    assert "disabled" in reason_before.lower()

    clock.advance(11)
    allowed_after, reason_after = breaker.can_execute("exec_command", "s1")
    assert allowed_after is True
    assert reason_after == ""


def test_session_level_breaker_blocks_all_tools_for_session() -> None:
    clock = Clock()
    breaker = CircuitBreaker(_config(), now_fn=clock.now)

    for _ in range(5):
        breaker.record_failure(tool_name="exec_command", session_id="s1")

    allowed, reason = breaker.can_execute("run_code", "s1")
    assert allowed is False
    assert "session" in reason

    allowed_other, _ = breaker.can_execute("run_code", "s2")
    assert allowed_other is True


def test_global_kill_switch_blocks_immediately() -> None:
    breaker = CircuitBreaker(_config())
    breaker.set_global_kill_switch(True)

    allowed, reason = breaker.can_execute("exec_command", "s1")
    assert allowed is False
    assert "kill switch" in reason.lower()


def test_skill_level_breaker_fuses_only_matching_logical_skill() -> None:
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            tool_level_max_failures=3,
            session_level_max_failures=5,
            cooldown_seconds=10,
            global_kill_switch=False,
            skill_level_enabled=True,
            skill_level_max_failures=2,
        )
    )

    breaker.record_failure("exec_command", "s1", "git-workflow")
    breaker.record_failure("exec_command", "s1", "git-workflow")

    allowed_same, reason_same = breaker.can_execute("exec_command", "s1", "git-workflow")
    allowed_other, _ = breaker.can_execute("exec_command", "s1", "host-inspection")

    assert allowed_same is False
    assert "logical skill 'git-workflow'" in reason_same
    assert allowed_other is True


def test_skill_level_breaker_reset_on_success() -> None:
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            tool_level_max_failures=5,
            session_level_max_failures=5,
            cooldown_seconds=10,
            global_kill_switch=False,
            skill_level_enabled=True,
            skill_level_max_failures=2,
        )
    )

    breaker.record_failure("exec_command", "s1", "git-workflow")
    breaker.record_success("exec_command", "s1", "git-workflow")
    breaker.record_failure("exec_command", "s1", "git-workflow")

    allowed, reason = breaker.can_execute("exec_command", "s1", "git-workflow")

    assert allowed is True
    assert reason == ""


def test_skill_level_breaker_recovers_after_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            tool_level_max_failures=5,
            session_level_max_failures=5,
            cooldown_seconds=10,
            global_kill_switch=False,
            skill_level_enabled=True,
            skill_level_max_failures=2,
        ),
        now_fn=clock.now,
    )

    breaker.record_failure("exec_command", "s1", "git-workflow")
    breaker.record_failure("exec_command", "s1", "git-workflow")

    allowed_before, reason_before = breaker.can_execute("exec_command", "s1", "git-workflow")
    assert allowed_before is False
    assert "logical skill 'git-workflow'" in reason_before

    clock.advance(11)
    allowed_after, reason_after = breaker.can_execute("exec_command", "s1", "git-workflow")
    assert allowed_after is True
    assert reason_after == ""
