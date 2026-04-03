from __future__ import annotations

import inspect
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal

import structlog
import yaml

from hypo_agent.exceptions import HypoAgentError
from hypo_agent.models import SkillOutput
from hypo_agent.security.permission_manager import PermissionManager
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.core.skill_manager")
_SKILL_MANAGER_ERRORS = (HypoAgentError, OSError, RuntimeError, TypeError, ValueError)

BuiltinToolHandler = Callable[..., Awaitable[SkillOutput] | SkillOutput]


class SkillManager:
    _OPERATION_OVERRIDES: dict[str, Literal["read", "write", "execute"]] = {
        "read_file": "read",
        "write_file": "write",
        "list_directory": "read",
        "scan_directory": "write",
        "get_directory_index": "read",
        "update_directory_description": "read",
    }

    def __init__(
        self,
        skills: list[BaseSkill] | None = None,
        *,
        circuit_breaker: Any | None = None,
        permission_manager: PermissionManager | None = None,
        structured_store: Any | None = None,
    ) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self._skill_sources: dict[str, str] = {}
        self._tool_to_skill: dict[str, BaseSkill] = {}
        self._builtin_tools: dict[str, tuple[dict[str, Any], BuiltinToolHandler, str]] = {}
        self._circuit_breaker = circuit_breaker
        self._permission_manager = permission_manager
        self._structured_store = structured_store
        if skills:
            self.register_many(skills)

    def register(self, skill: BaseSkill, *, source: str = "auto") -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' already registered")

        self._skills[skill.name] = skill
        self._skill_sources[skill.name] = str(source or "auto")
        for tool in skill.tools:
            tool_name = self._read_tool_name(tool)
            if not tool_name:
                raise ValueError(f"Skill '{skill.name}' has tool without function.name")
            if tool_name in self._tool_to_skill:
                raise ValueError(f"Tool '{tool_name}' already registered")
            self._tool_to_skill[tool_name] = skill
        logger.info(
            "skill_manager.register",
            skill_name=skill.name,
            source=self._skill_sources[skill.name],
            tools=[self._read_tool_name(tool) for tool in skill.tools],
        )
        logger.info(
            "skill.registered",
            skill_name=skill.name,
            source=self._skill_sources[skill.name],
            tools=[self._read_tool_name(tool) for tool in skill.tools],
        )

    def register_many(self, skills: list[BaseSkill], *, source: str = "auto") -> None:
        for skill in skills:
            self.register(skill, source=source)

    def get_tools_schema(self) -> list[dict[str, Any]]:
        all_tools: list[dict[str, Any]] = []
        for schema, _, _ in self._builtin_tools.values():
            all_tools.append(schema)
        for skill in self._skills.values():
            all_tools.extend(skill.tools)
        return all_tools

    def register_builtin_tool(
        self,
        schema: dict[str, Any],
        handler: BuiltinToolHandler,
        *,
        source: str = "builtin",
    ) -> None:
        tool_name = self._read_tool_name(schema)
        if not tool_name:
            raise ValueError("Builtin tool schema must declare function.name")
        if tool_name in self._tool_to_skill or tool_name in self._builtin_tools:
            raise ValueError(f"Tool '{tool_name}' already registered")
        self._builtin_tools[tool_name] = (schema, handler, source)

    def list_skills(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for skill in self._skills.values():
            items.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "enabled": True,
                    "source": self._skill_sources.get(skill.name, "auto"),
                    "tools": [self._read_tool_name(tool) for tool in skill.tools],
                }
            )
        return items

    def registration_snapshot(self) -> list[dict[str, Any]]:
        return self.list_skills()

    @staticmethod
    def known_skill_names(path: Path | str = "config/skills.yaml") -> set[str]:
        payload = SkillManager._load_skills_payload(path)
        configured_skills = payload.get("skills", {})
        if not isinstance(configured_skills, dict):
            return set()
        return {str(name) for name in configured_skills.keys()}

    @staticmethod
    def find_enabled_skills(path: Path | str = "config/skills.yaml") -> set[str]:
        payload = SkillManager._load_skills_payload(path)
        configured_skills = payload.get("skills", {})
        if not isinstance(configured_skills, dict):
            return set()

        enabled: set[str] = set()
        for name, cfg in configured_skills.items():
            if isinstance(cfg, dict) and bool(cfg.get("enabled", False)):
                enabled.add(str(name))
        return enabled

    @staticmethod
    def _load_skills_payload(path: Path | str) -> dict[str, Any]:
        config_path = Path(path)
        if not config_path.exists():
            return {}
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else {}

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
        skill_name: str | None = None,
    ) -> SkillOutput:
        effective_skill_name = self._resolve_invocation_skill_name(tool_name, skill_name)
        logger.info(
            "skill.invoke.start",
            tool_name=tool_name,
            session_id=session_id,
            skill_name=effective_skill_name,
        )
        started_at = perf_counter()

        if self._circuit_breaker is not None:
            kill_getter = getattr(self._circuit_breaker, "get_global_kill_switch", None)
            kill_active = bool(kill_getter()) if callable(kill_getter) else False
            if kill_active:
                reason = "Kill Switch is active"
                logger.warning(
                    "skill.invoke.blocked",
                    tool_name=tool_name,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    reason=reason,
                )
                blocked = SkillOutput(status="error", error_info=reason)
                invocation_id = await self._record_tool_invocation(
                    tool_name=tool_name,
                    params=params,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    status="blocked",
                    result=None,
                    error_info=reason,
                    duration_ms=self._duration_ms(started_at),
                )
                self._attach_invocation_id(blocked, invocation_id)
                return blocked

            allowed, reason = self._breaker_can_execute(
                tool_name,
                session_id,
                effective_skill_name,
            )
            if not allowed:
                logger.warning(
                    "skill.invoke.blocked",
                    tool_name=tool_name,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    reason=reason,
                )
                status = "fused" if self._is_fused_reason(reason) else "error"
                blocked = SkillOutput(status=status, error_info=reason)
                invocation_id = await self._record_tool_invocation(
                    tool_name=tool_name,
                    params=params,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    status="blocked",
                    result=None,
                    error_info=reason,
                    duration_ms=self._duration_ms(started_at),
                )
                self._attach_invocation_id(blocked, invocation_id)
                return blocked

        skill = self._tool_to_skill.get(tool_name)
        builtin = self._builtin_tools.get(tool_name)
        if skill is None and builtin is None:
            result = SkillOutput(
                status="error",
                error_info=f"Unknown tool '{tool_name}'",
            )
            logger.warning(
                "skill.invoke.fail",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
                status=result.status,
                error=result.error_info,
            )
            invocation_id = await self._record_tool_invocation(
                tool_name=tool_name,
                params=params,
                session_id=session_id,
                skill_name=effective_skill_name,
                status="error",
                result=None,
                error_info=result.error_info,
                duration_ms=self._duration_ms(started_at),
            )
            self._attach_invocation_id(result, invocation_id)
            return result

        if (
            self._permission_manager is not None
            and skill is not None
            and skill.required_permissions
            and isinstance(params.get("path"), str)
        ):
            path = str(params["path"])
            operation = self._infer_operation(tool_name)
            allowed, reason = self._permission_manager.check_permission(path, operation)
            if not allowed:
                logger.warning(
                    "skill.invoke.blocked.permission",
                    tool_name=tool_name,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    path=path,
                    operation=operation,
                    reason=reason,
                )
                blocked = SkillOutput(
                    status="error",
                    error_info=f"Permission denied: {reason}",
                )
                invocation_id = await self._record_tool_invocation(
                    tool_name=tool_name,
                    params=params,
                    session_id=session_id,
                    skill_name=effective_skill_name,
                    status="blocked",
                    result=None,
                    error_info=reason,
                    duration_ms=self._duration_ms(started_at),
                )
                self._attach_invocation_id(blocked, invocation_id)
                return blocked

        internal_params = dict(params)
        if session_id is not None:
            internal_params.setdefault("__session_id", session_id)

        try:
            if builtin is not None:
                _, handler, _ = builtin
                result = handler(params, session_id=session_id)
                if inspect.isawaitable(result):
                    result = await result
            else:
                assert skill is not None
                result = await skill.execute(tool_name, internal_params)
        except _SKILL_MANAGER_ERRORS as exc:
            logger.error(
                "skill.invoke.exception",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
                error=str(exc),
            )
            if self._circuit_breaker is not None:
                self._breaker_record_failure(tool_name, session_id, effective_skill_name)
            result = SkillOutput(status="error", error_info=str(exc))
            logger.warning(
                "skill.invoke.fail",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
                status=result.status,
                error=result.error_info,
            )
            invocation_id = await self._record_tool_invocation(
                tool_name=tool_name,
                params=params,
                session_id=session_id,
                skill_name=effective_skill_name,
                status="error",
                result=None,
                error_info=result.error_info,
                duration_ms=self._duration_ms(started_at),
            )
            self._attach_invocation_id(result, invocation_id)
            return result

        if not isinstance(result, SkillOutput):
            if self._circuit_breaker is not None:
                self._breaker_record_failure(tool_name, session_id, effective_skill_name)
            normalized = SkillOutput(
                status="error",
                error_info=(
                    f"Skill '{skill.name}' returned invalid output"
                    if skill is not None
                    else f"Builtin tool '{tool_name}' returned invalid output"
                ),
            )
            logger.warning(
                "skill.invoke.fail",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
                status=normalized.status,
                error=normalized.error_info,
            )
            invocation_id = await self._record_tool_invocation(
                tool_name=tool_name,
                params=params,
                session_id=session_id,
                skill_name=effective_skill_name,
                status="error",
                result=None,
                error_info=normalized.error_info,
                duration_ms=self._duration_ms(started_at),
            )
            self._attach_invocation_id(normalized, invocation_id)
            return normalized

        if self._circuit_breaker is not None:
            if result.status == "success":
                self._breaker_record_success(tool_name, session_id, effective_skill_name)
            else:
                self._breaker_record_failure(tool_name, session_id, effective_skill_name)
                allowed_after, reason_after = self._breaker_can_execute(
                    tool_name,
                    session_id,
                    effective_skill_name,
                )
                if (not allowed_after) and self._is_fused_reason(reason_after):
                    result = SkillOutput(status="fused", error_info=reason_after)

        if result.status == "success":
            logger.info(
                "skill.invoke.ok",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
            )
        else:
            logger.warning(
                "skill.invoke.fail",
                tool_name=tool_name,
                session_id=session_id,
                skill_name=effective_skill_name,
                status=result.status,
                error=result.error_info,
            )

        invocation_id = await self._record_tool_invocation(
            tool_name=tool_name,
            params=params,
            session_id=session_id,
            skill_name=effective_skill_name,
            status=result.status,
            result=result.result,
            error_info=result.error_info,
            duration_ms=self._duration_ms(started_at),
        )
        self._attach_invocation_id(result, invocation_id)
        return result

    def _infer_operation(self, tool_name: str) -> Literal["read", "write", "execute"]:
        lowered = tool_name.lower()
        override = self._OPERATION_OVERRIDES.get(lowered)
        if override is not None:
            return override
        if "write" in lowered or lowered.startswith("update_"):
            return "write"
        if "execute" in lowered or lowered.startswith("run_"):
            return "execute"
        return "read"

    def _read_tool_name(self, tool: dict[str, Any]) -> str:
        function_payload = tool.get("function")
        if not isinstance(function_payload, dict):
            return ""
        name = function_payload.get("name")
        return str(name) if isinstance(name, str) else ""

    async def _record_tool_invocation(
        self,
        *,
        tool_name: str,
        params: dict[str, Any],
        session_id: str | None,
        skill_name: str,
        status: str,
        result: Any,
        error_info: str | None,
        duration_ms: float,
    ) -> int | None:
        if self._structured_store is None or not session_id:
            return None

        normalized_status = self._normalize_invocation_status(status)
        try:
            return await self._structured_store.record_tool_invocation(
                session_id=session_id,
                tool_name=tool_name,
                skill_name=skill_name,
                params_json=self._serialize_for_storage(params),
                status=normalized_status,
                result_summary=self._build_result_preview(result, error_info),
                duration_ms=duration_ms,
                error_info=error_info,
                compressed_meta_json=None,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover - defensive safeguard
            logger.warning(
                "skill_manager.record.failed",
                tool_name=tool_name,
                session_id=session_id,
                error=str(exc),
            )
            return None

    def _is_fused_reason(self, reason: str) -> bool:
        lowered = reason.lower()
        return ("disabled" in lowered) or ("session circuit breaker" in lowered)

    def _resolve_invocation_skill_name(
        self,
        tool_name: str,
        skill_name: str | None,
    ) -> str:
        normalized = str(skill_name or "").strip()
        if normalized:
            return normalized
        skill = self._tool_to_skill.get(tool_name)
        if skill is not None:
            return skill.name
        builtin = self._builtin_tools.get(tool_name)
        if builtin is not None:
            return builtin[2]
        return "direct"

    def _breaker_can_execute(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str,
    ) -> tuple[bool, str]:
        assert self._circuit_breaker is not None
        try:
            return self._circuit_breaker.can_execute(tool_name, session_id, skill_name)
        except TypeError:
            return self._circuit_breaker.can_execute(tool_name, session_id)

    def _breaker_record_success(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str,
    ) -> None:
        assert self._circuit_breaker is not None
        try:
            self._circuit_breaker.record_success(tool_name, session_id, skill_name)
        except TypeError:
            self._circuit_breaker.record_success(tool_name, session_id)

    def _breaker_record_failure(
        self,
        tool_name: str,
        session_id: str | None,
        skill_name: str,
    ) -> None:
        assert self._circuit_breaker is not None
        try:
            self._circuit_breaker.record_failure(tool_name, session_id, skill_name)
        except TypeError:
            self._circuit_breaker.record_failure(tool_name, session_id)

    def _normalize_invocation_status(self, status: str) -> str:
        lowered = status.lower()
        if lowered in {"success", "error", "timeout", "blocked", "fused"}:
            return lowered
        return "error"

    def _build_result_preview(self, result: Any, error_info: str | None) -> str:
        if result is not None:
            return self._serialize_for_storage(result)[:500]
        if error_info:
            return error_info[:500]
        return ""

    def _attach_invocation_id(
        self,
        output: SkillOutput,
        invocation_id: int | None,
    ) -> None:
        if invocation_id is None:
            return
        output.metadata["invocation_id"] = invocation_id

    def _serialize_for_storage(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    def _duration_ms(self, started_at: float) -> float:
        return max((perf_counter() - started_at) * 1000.0, 0.0)

    async def attach_invocation_compressed_meta(
        self,
        *,
        invocation_id: int,
        compressed_meta: dict[str, Any],
    ) -> None:
        if self._structured_store is None:
            return

        try:
            payload = self._serialize_for_storage(compressed_meta)
            result = self._structured_store.update_tool_invocation_compressed_meta(
                invocation_id,
                compressed_meta_json=payload,
            )
            if inspect.isawaitable(result):
                await result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover - defensive safeguard
            logger.warning(
                "skill_manager.record_compressed_meta.failed",
                invocation_id=invocation_id,
                error=str(exc),
            )
