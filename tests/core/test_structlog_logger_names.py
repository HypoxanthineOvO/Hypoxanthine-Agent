from __future__ import annotations

from hypo_agent.core import pipeline, skill_manager
from hypo_agent.gateway import kill_switch_api
from hypo_agent.security import circuit_breaker, permission_manager
from hypo_agent.skills import code_run_skill, fs_skill


def test_backend_modules_use_named_structlog_loggers() -> None:
    modules = {
        "hypo_agent.core.pipeline": pipeline,
        "hypo_agent.core.skill_manager": skill_manager,
        "hypo_agent.gateway.kill_switch_api": kill_switch_api,
        "hypo_agent.security.circuit_breaker": circuit_breaker,
        "hypo_agent.security.permission_manager": permission_manager,
        "hypo_agent.skills.code_run_skill": code_run_skill,
        "hypo_agent.skills.fs_skill": fs_skill,
    }

    for expected_name, module in modules.items():
        logger = getattr(module, "logger")
        assert logger._logger_factory_args == (expected_name,)
