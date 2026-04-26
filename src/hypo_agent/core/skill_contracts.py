from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProbeGate = Literal["unit", "contract", "test_mode_probe", "optional_integration"]

_GATES: tuple[ProbeGate, ...] = ("unit", "contract", "test_mode_probe", "optional_integration")
_UNIT_TEST_FILE_BY_SKILL: dict[str, str] = {
    "agent_search": "test_agent_search_skill.py",
    "auth": "test_auth_skill.py",
    "code_run": "test_code_run_skill.py",
    "coder": "test_coder_skill.py",
    "email_scanner": "test_email_scanner_skill.py",
    "exec": "test_exec_skill.py",
    "filesystem": "test_fs_skill.py",
    "info": "test_info_portal_skill.py",
    "info_reach": "test_info_reach_fallback.py",
    "log_inspector": "test_log_inspector_skill.py",
    "memory": "test_memory_skill.py",
    "notion": "test_notion_skill.py",
    "probe": "test_probe_skill.py",
    "reminder": "test_reminder_skill.py",
    "tmux": "test_tmux_skill.py",
}


class ContractValidationError(ValueError):
    """Raised when a skill contract is missing model-facing operational metadata."""


@dataclass(frozen=True, slots=True)
class AcceptanceProbe:
    name: str
    gate: ProbeGate
    command: str
    requires_test_mode: bool = False
    touches_production: bool = False

    def validate(self) -> None:
        issues: list[str] = []
        if not self.name.strip():
            issues.append("name")
        if self.gate not in _GATES:
            issues.append("gate")
        if not self.command.strip():
            issues.append("command")
        if self.gate == "test_mode_probe" and not self.requires_test_mode:
            issues.append("requires_test_mode")
        if self.gate == "test_mode_probe" and self.touches_production:
            issues.append("touches_production")
        if issues:
            raise ContractValidationError(f"Invalid acceptance probe: {', '.join(issues)}")


@dataclass(frozen=True, slots=True)
class ToolContract:
    tool_name: str
    description: str
    parameters_schema: dict[str, Any]
    operation: str
    side_effect_class: str
    timeout_seconds: int
    retryable: bool
    repair_hints_zh: list[str]
    acceptance_probes: list[AcceptanceProbe] = field(default_factory=list)
    required_config: list[str] = field(default_factory=list)

    def validate(self) -> None:
        issues: list[str] = []
        if not self.tool_name.strip():
            issues.append("tool_name")
        if not self.description.strip():
            issues.append("description")
        if not isinstance(self.parameters_schema, dict) or not self.parameters_schema:
            issues.append("parameters_schema")
        if not self.operation.strip():
            issues.append("operation")
        if not self.side_effect_class.strip():
            issues.append("side_effect_class")
        if self.timeout_seconds <= 0:
            issues.append("timeout_seconds")
        if not [hint for hint in self.repair_hints_zh if hint.strip()]:
            issues.append("repair_hints_zh")
        if not self.acceptance_probes:
            issues.append("acceptance_probes")
        for probe in self.acceptance_probes:
            try:
                probe.validate()
            except ContractValidationError as exc:
                issues.append(str(exc))
        if issues:
            raise ContractValidationError(
                f"Invalid contract for tool '{self.tool_name or '<unknown>'}': {', '.join(issues)}"
            )


@dataclass(frozen=True, slots=True)
class SkillContract:
    skill_name: str
    description: str
    required_permissions: list[str]
    tools: list[ToolContract]
    compatibility: str = "compatible"

    def validate(self) -> None:
        issues: list[str] = []
        if not self.skill_name.strip():
            issues.append("skill_name")
        if not self.description.strip():
            issues.append("description")
        if not self.tools:
            issues.append("tools")
        seen: set[str] = set()
        for tool in self.tools:
            if tool.tool_name in seen:
                issues.append(f"duplicate tool '{tool.tool_name}'")
            seen.add(tool.tool_name)
            try:
                tool.validate()
            except ContractValidationError as exc:
                issues.append(str(exc))
        if issues:
            raise ContractValidationError(
                f"Invalid contract for skill '{self.skill_name or '<unknown>'}': {', '.join(issues)}"
            )


def build_contract_from_skill(skill: Any) -> SkillContract:
    skill_name = str(getattr(skill, "name", "") or "").strip()
    tools = []
    for schema in getattr(skill, "tools"):
        function = schema.get("function", {}) if isinstance(schema, dict) else {}
        tool_name = str(function.get("name") or "").strip()
        operation = _infer_operation(skill_name, tool_name)
        tools.append(
            ToolContract(
                tool_name=tool_name,
                description=str(function.get("description") or "").strip(),
                parameters_schema=dict(function.get("parameters") or {"type": "object"}),
                operation=operation,
                side_effect_class=_infer_side_effect_class(operation),
                timeout_seconds=_infer_timeout_seconds(operation, tool_name),
                retryable=_infer_retryable(operation, tool_name),
                repair_hints_zh=_repair_hints(skill_name, tool_name, operation),
                acceptance_probes=_acceptance_probes(skill_name, tool_name, operation),
                required_config=_required_config(skill_name),
            )
        )
    return SkillContract(
        skill_name=skill_name,
        description=str(getattr(skill, "description", "") or "").strip(),
        required_permissions=list(getattr(skill, "required_permissions", []) or []),
        tools=tools,
    )


def build_acceptance_report(contracts: list[SkillContract]) -> dict[str, Any]:
    summary = {gate: {"total": 0, "defined": 0, "optional": 0, "missing": 0} for gate in _GATES}
    skills: list[dict[str, Any]] = []

    for contract in contracts:
        skill_entry = {
            "skill_name": contract.skill_name,
            "tools": [],
        }
        for tool in contract.tools:
            probes_by_gate: dict[str, list[AcceptanceProbe]] = {gate: [] for gate in _GATES}
            for probe in tool.acceptance_probes:
                probes_by_gate.setdefault(probe.gate, []).append(probe)

            gates: dict[str, dict[str, Any]] = {}
            for gate in _GATES:
                probes = probes_by_gate.get(gate, [])
                status = "missing"
                if probes:
                    status = "optional" if gate == "optional_integration" else "defined"
                summary[gate]["total"] += len(probes)
                summary[gate][status] += 1
                gates[gate] = {
                    "status": status,
                    "commands": [probe.command for probe in probes],
                }

            skill_entry["tools"].append(
                {
                    "tool_name": tool.tool_name,
                    "operation": tool.operation,
                    "side_effect_class": tool.side_effect_class,
                    "gates": gates,
                }
            )
        skills.append(skill_entry)

    return {"summary": summary, "skills": skills}


def _infer_operation(skill_name: str, tool_name: str) -> str:
    lowered = f"{skill_name}:{tool_name}".lower()
    if any(token in lowered for token in ("exec", "run_code", "tmux_send", "command", "script")):
        return "execute"
    if any(token in lowered for token in ("write", "update", "create", "delete", "save_", "scan_")):
        return "write"
    if any(token in lowered for token in ("auth_login", "auth_check", "browser")):
        return "auth"
    if any(token in lowered for token in ("search", "query", "notion", "email")):
        return "network"
    if any(token in lowered for token in ("read", "get_", "list_", "export")):
        return "read"
    return "read"


def _infer_side_effect_class(operation: str) -> str:
    if operation in {"write", "execute", "auth", "network", "read"}:
        return operation
    return "read"


def _infer_timeout_seconds(operation: str, tool_name: str) -> int:
    lowered = tool_name.lower()
    if operation == "execute":
        return 60
    if operation in {"network", "auth"}:
        return 30
    if operation == "write" or "scan" in lowered:
        return 20
    return 10


def _infer_retryable(operation: str, tool_name: str) -> bool:
    lowered = tool_name.lower()
    return operation in {"network", "auth"} or "search" in lowered or "query" in lowered


def _required_config(skill_name: str) -> list[str]:
    if skill_name == "notion":
        return ["config/secrets.yaml:services.notion.integration_secret"]
    if skill_name == "probe":
        return ["config/services.yaml:services.probe.token"]
    return []


def _repair_hints(skill_name: str, tool_name: str, operation: str) -> list[str]:
    hints = ["请检查必填参数是否完整，并根据工具返回的错误信息修正后再试。"]
    if "file" in tool_name or skill_name == "filesystem":
        hints.append("请确认路径存在、路径拼写正确，并且当前权限策略允许访问该路径。")
    if skill_name == "notion" or tool_name.startswith("notion_"):
        hints.append("请先调用 notion_get_schema 确认数据库字段名，再使用完全一致的字段名。")
    if operation == "execute":
        hints.append("请使用非交互式命令，设置合理 timeout，并避免连接生产端口 8765。")
    if operation == "network":
        hints.append("外部服务失败时先确认配置、网络和 schema；超时可稍后重试。")
    return hints


def _acceptance_probes(skill_name: str, tool_name: str, operation: str) -> list[AcceptanceProbe]:
    normalized_skill = skill_name.replace("-", "_")
    unit_test_file = _UNIT_TEST_FILE_BY_SKILL.get(
        normalized_skill,
        f"test_{normalized_skill}_skill.py",
    )
    probes = [
        AcceptanceProbe(
            name=f"{tool_name} unit",
            gate="unit",
            command=f"uv run pytest tests/skills/{unit_test_file} -q",
        ),
        AcceptanceProbe(
            name=f"{tool_name} contract",
            gate="contract",
            command="uv run pytest tests/skills/test_skill_contracts.py -q",
        ),
        AcceptanceProbe(
            name=f"{tool_name} test-mode probe",
            gate="test_mode_probe",
            command="HYPO_TEST_MODE=1 uv run pytest tests/scripts/test_agent_cli_smoke_qq.py -q",
            requires_test_mode=True,
        ),
    ]
    if operation in {"network", "auth"}:
        probes.append(
            AcceptanceProbe(
                name=f"{tool_name} optional integration",
                gate="optional_integration",
                command=f"uv run pytest -m integration tests/skills/test_{normalized_skill}_skill.py -q",
            )
        )
    return probes
