from __future__ import annotations

from pathlib import Path

import pytest

from hypo_agent.core.skill_contracts import (
    AcceptanceProbe,
    ContractValidationError,
    SkillContract,
    ToolContract,
    build_acceptance_report,
    build_contract_from_skill,
)
from hypo_agent.skills.exec_skill import ExecSkill
from hypo_agent.skills.fs_skill import FileSystemSkill
from hypo_agent.skills.memory_skill import MemorySkill


class FakeStore:
    db_path = Path("/tmp/hypo-agent-test.db")


def test_tool_contract_rejects_missing_operational_metadata() -> None:
    contract = ToolContract(
        tool_name="read_file",
        description="Read a file",
        parameters_schema={"type": "object"},
        operation="",
        side_effect_class="",
        timeout_seconds=0,
        retryable=False,
        repair_hints_zh=[],
        acceptance_probes=[],
    )

    with pytest.raises(ContractValidationError) as exc_info:
        contract.validate()

    message = str(exc_info.value)
    assert "operation" in message
    assert "side_effect_class" in message
    assert "timeout_seconds" in message
    assert "repair_hints_zh" in message


def test_build_contract_from_filesystem_skill_infers_tool_metadata(tmp_path: Path) -> None:
    skill = FileSystemSkill(permission_manager=None, index_file=tmp_path / "directory_index.yaml")

    contract = build_contract_from_skill(skill)
    tools = {tool.tool_name: tool for tool in contract.tools}

    assert contract.skill_name == "filesystem"
    assert tools["read_file"].operation == "read"
    assert tools["read_file"].side_effect_class == "read"
    assert tools["write_file"].operation == "write"
    assert tools["write_file"].side_effect_class == "write"
    assert tools["scan_directory"].operation == "write"
    assert any("路径" in hint for hint in tools["read_file"].repair_hints_zh)
    assert any("test_fs_skill.py" in probe.command for probe in tools["read_file"].acceptance_probes)
    assert all(tool.acceptance_probes for tool in contract.tools)


def test_core_skill_contracts_validate_with_required_gates(tmp_path: Path) -> None:
    contracts = [
        build_contract_from_skill(
            ExecSkill(sandbox_dir=tmp_path / "sandbox", exec_profiles_path=tmp_path / "missing.yaml")
        ),
        build_contract_from_skill(
            FileSystemSkill(permission_manager=None, index_file=tmp_path / "directory_index.yaml")
        ),
        build_contract_from_skill(MemorySkill(structured_store=FakeStore())),
    ]

    for contract in contracts:
        contract.validate()

    exec_contract = next(contract for contract in contracts if contract.skill_name == "exec")
    exec_command = next(tool for tool in exec_contract.tools if tool.tool_name == "exec_command")
    gates = {probe.gate for probe in exec_command.acceptance_probes}
    assert {"unit", "contract", "test_mode_probe"}.issubset(gates)


def test_acceptance_report_separates_unit_contract_probe_and_integration() -> None:
    contract = SkillContract(
        skill_name="sample",
        description="Sample skill",
        required_permissions=[],
        tools=[
            ToolContract(
                tool_name="sample_read",
                description="Read sample state",
                parameters_schema={"type": "object"},
                operation="read",
                side_effect_class="read",
                timeout_seconds=10,
                retryable=False,
                repair_hints_zh=["请检查输入参数。"],
                acceptance_probes=[
                    AcceptanceProbe(name="unit", gate="unit", command="pytest tests/skills/test_sample.py"),
                    AcceptanceProbe(name="contract", gate="contract", command="pytest tests/contracts/test_sample.py"),
                    AcceptanceProbe(
                        name="test-mode smoke",
                        gate="test_mode_probe",
                        command="HYPO_TEST_MODE=1 pytest tests/probes/test_sample.py",
                        requires_test_mode=True,
                    ),
                    AcceptanceProbe(
                        name="sandbox integration",
                        gate="optional_integration",
                        command="pytest -m integration tests/skills/test_sample.py",
                    ),
                ],
            )
        ],
    )

    report = build_acceptance_report([contract])

    assert report["summary"]["unit"]["total"] == 1
    assert report["summary"]["contract"]["total"] == 1
    assert report["summary"]["test_mode_probe"]["total"] == 1
    assert report["summary"]["optional_integration"]["total"] == 1
    assert report["skills"][0]["tools"][0]["gates"]["unit"]["status"] == "defined"
    assert report["skills"][0]["tools"][0]["gates"]["optional_integration"]["status"] == "optional"
