from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hypo_agent.channels.probe import ProbeServer
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.skill_catalog import SkillCatalog, SkillManifest
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.memory.email_store import EmailStore
from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message
from hypo_agent.skills import (
    AgentSearchSkill,
    CodeRunSkill,
    CoderSkill,
    EmailScannerSkill,
    ExecSkill,
    FileSystemSkill,
    InfoPortalSkill,
    InfoReachSkill,
    MemorySkill,
    NotionPlanSkill,
    NotionSkill,
    ProbeSkill,
    ReminderSkill,
    TmuxSkill,
)


@dataclass(frozen=True, slots=True)
class MatchCase:
    skill_name: str
    message: str
    key_tool: str


@dataclass(slots=True)
class SkillCheck:
    name: str
    category: str
    phase: str
    frontmatter_ok: bool
    body_ok: bool
    tools_ok: bool
    profile_ok: bool
    references_ok: bool
    status: str
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineMatchCheck:
    skill_name: str
    message: str
    matched: bool
    injected: bool
    tool_present: bool
    candidates: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConflictCheck:
    trigger: str
    skills: list[str]
    severity: str
    note: str


@dataclass(slots=True)
class VerificationReport:
    skill_checks: list[SkillCheck]
    pipeline_checks: list[PipelineMatchCheck]
    conflicts: list[ConflictCheck]
    registered_tools: list[str]
    acceptance_report: dict[str, Any]

    @property
    def ok(self) -> bool:
        return (
            all(item.status == "ok" for item in self.skill_checks)
            and all(item.matched and item.injected and item.tool_present for item in self.pipeline_checks)
            and all(item.severity != "severe" for item in self.conflicts)
        )


MATCH_CASES: list[MatchCase] = [
    MatchCase("git-workflow", "帮我看看这个 repo 最近的 commit 历史", "exec_command"),
    MatchCase("system-service-ops", "hypo-agent 服务状态怎么样", "exec_command"),
    MatchCase("python-project-dev", "跑一下 pytest 看看测试结果", "exec_command"),
    MatchCase("hypo-agent-ops", "用测试模式跑一下 smoke test", "exec_command"),
    MatchCase("host-inspection", "服务器磁盘和内存现在什么情况", "exec_command"),
    MatchCase("weather", "帮我查一下北京天气", "exec_command"),
    MatchCase("agent-browser", "打开这个网页并点一下页面里的按钮", "exec_command"),
    MatchCase("github-ops", "帮我看看这个仓库有哪些 open PR", "exec_command"),
    MatchCase("log-inspector", "查看最近的错误日志", "read_file"),
    MatchCase("agent-search", "搜索一下 Claude 4 最新消息", "search_web"),
    MatchCase("info-portal", "今天有什么 AI 新闻", "info_today"),
    MatchCase("notion", "帮我在 Notion 创建一个页面", "notion_create_entry"),
    MatchCase("coder", "提交一个代码任务给 Coder", "coder_submit_task"),
    MatchCase("probe", "看看探针设备列表", "probe_list_devices"),
    MatchCase("info-reach", "帮我订阅 LLM 相关资讯", "info_subscribe"),
]


class _DummyScheduler:
    async def register_reminder_job(self, reminder: Any) -> None:
        del reminder

    async def remove_reminder_job(self, reminder_id: Any) -> None:
        del reminder_id


def build_runtime_skill_manager(repo_root: Path) -> SkillManager:
    with tempfile.TemporaryDirectory(prefix="verify-skills-") as tmp_dir:
        temp_root = Path(tmp_dir)
        manager = SkillManager(
            skills=[
                ExecSkill(
                    sandbox_dir=temp_root / "exec-sandbox",
                    exec_profiles_path=repo_root / "config" / "exec_profiles.yaml",
                ),
                CodeRunSkill(
                    permission_manager=None,
                    sandbox_dir=temp_root / "code-run-sandbox",
                ),
                FileSystemSkill(
                    permission_manager=None,
                    index_file=temp_root / "directory_index.yaml",
                ),
                MemorySkill(structured_store=object()),
                TmuxSkill(permission_manager=None),
                ReminderSkill(structured_store=object(), scheduler=_DummyScheduler()),
                EmailScannerSkill(
                    structured_store=object(),
                    model_router=None,
                    message_queue=None,
                    attachments_root=temp_root / "email-attachments",
                    email_store=EmailStore(root=temp_root / "emails"),
                ),
                AgentSearchSkill(),
                InfoPortalSkill(info_client=object()),
                NotionSkill(notion_client=object()),
                NotionPlanSkill(notion_client=object(), plan_page_id="verify-plan"),
                CoderSkill(coder_client=object()),
                ProbeSkill(probe_server=ProbeServer(token="verify-token")),
                InfoReachSkill(
                    db_path=temp_root / "hypo.db",
                    base_url="http://localhost:8200",
                ),
            ]
        )
        return manager


def build_pipeline(repo_root: Path, catalog: SkillCatalog) -> ChatPipeline:
    with tempfile.TemporaryDirectory(prefix="verify-pipeline-") as tmp_dir:
        session_memory = SessionMemory(sessions_dir=Path(tmp_dir) / "sessions")
        return ChatPipeline(
            router=object(),
            chat_model="test-model",
            session_memory=session_memory,
            skill_catalog=catalog,
        )


def verify_repo(repo_root: Path) -> VerificationReport:
    catalog = SkillCatalog(repo_root / "skills", check_cli_availability=True)
    catalog.scan()
    manifests = catalog.list_manifests()
    runtime_manager = build_runtime_skill_manager(repo_root)
    registered_tools = sorted(
        tool["function"]["name"]
        for tool in runtime_manager.get_tools_schema()
        if tool.get("function", {}).get("name")
    )
    registered_tool_set = set(registered_tools)
    exec_profiles = _load_exec_profiles(repo_root / "config" / "exec_profiles.yaml")

    skill_checks: list[SkillCheck] = []
    for manifest in manifests:
        body = catalog.load_body(manifest.name)
        references_ok = True
        issues: list[str] = []
        try:
            catalog.load_references(manifest.name)
        except Exception as exc:
            references_ok = False
            issues.append(f"references unreadable: {exc}")

        missing_tools = [tool for tool in manifest.allowed_tools if tool not in registered_tool_set]
        tools_ok = not missing_tools
        if missing_tools:
            issues.append(f"missing tools: {', '.join(missing_tools)}")

        profile_ok = manifest.exec_profile is None or manifest.exec_profile in exec_profiles
        if not profile_ok:
            issues.append(f"missing exec profile: {manifest.exec_profile}")

        if manifest.io_format and manifest.io_format not in {"json-stdio", "text"}:
            issues.append(f"invalid io_format: {manifest.io_format}")

        if manifest.exec_profile == "cli-json":
            if not manifest.cli_package:
                issues.append("missing cli_package for cli-json skill")
            if not manifest.cli_commands:
                issues.append("missing cli_commands for cli-json skill")
            if not manifest.io_format:
                issues.append("missing io_format for cli-json skill")

        if not manifest.available:
            issues.append(f"unavailable: {manifest.unavailable_reason}")

        body_ok = len(body.strip()) > 100
        if not body_ok:
            issues.append("body too short")

        frontmatter_ok = bool(
            manifest.name and manifest.description and manifest.allowed_tools and manifest.category
        )
        if not frontmatter_ok:
            issues.append("frontmatter incomplete")

        skill_checks.append(
            SkillCheck(
                name=manifest.name,
                category=manifest.category,
                phase=_phase_for_manifest(manifest),
                frontmatter_ok=frontmatter_ok,
                body_ok=body_ok,
                tools_ok=tools_ok,
                profile_ok=profile_ok,
                references_ok=references_ok,
                status="ok" if not issues else "fail",
                issues=issues,
            )
        )

    pipeline_checks = _verify_pipeline_injection(repo_root, catalog)
    conflicts = analyze_trigger_conflicts(manifests)
    return VerificationReport(
        skill_checks=skill_checks,
        pipeline_checks=pipeline_checks,
        conflicts=conflicts,
        registered_tools=registered_tools,
        acceptance_report=runtime_manager.get_skill_acceptance_report(),
    )


def analyze_trigger_conflicts(manifests: list[SkillManifest]) -> list[ConflictCheck]:
    trigger_map: dict[str, set[str]] = {}
    for manifest in manifests:
        if manifest.category == "internal":
            continue
        for trigger in manifest.triggers:
            normalized = trigger.strip().casefold()
            if not normalized:
                continue
            trigger_map.setdefault(normalized, set()).add(manifest.name)

    conflicts: list[ConflictCheck] = []
    for trigger, skills in sorted(trigger_map.items()):
        if len(skills) < 2:
            continue
        severity = "medium" if len(skills) == 2 else "severe"
        conflicts.append(
            ConflictCheck(
                trigger=trigger,
                skills=sorted(skills),
                severity=severity,
                note=(
                    "Multiple candidate skills may inject together; acceptable if ranking remains clear."
                    if severity != "severe"
                    else "High-overlap trigger shared by many skills; review recommended."
                ),
            )
        )
    return conflicts


def _verify_pipeline_injection(repo_root: Path, catalog: SkillCatalog) -> list[PipelineMatchCheck]:
    pipeline = build_pipeline(repo_root, catalog)
    checks: list[PipelineMatchCheck] = []
    for case in MATCH_CASES:
        inbound = Message(
            text=case.message,
            sender="tester",
            session_id="verify-session",
            channel="webui",
        )
        candidates = pipeline._match_skill_candidates(inbound)
        candidate_names = [item.name for item in candidates]
        instructions = pipeline._skill_instructions_context(candidates)
        checks.append(
            PipelineMatchCheck(
                skill_name=case.skill_name,
                message=case.message,
                matched=case.skill_name in candidate_names,
                injected=f"[Skill: {case.skill_name}]" in instructions,
                tool_present=case.key_tool in instructions,
                candidates=candidate_names,
            )
        )
    return checks


def _load_exec_profiles(path: Path) -> set[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict):
        return set()
    return {str(name) for name in profiles.keys()}


def _phase_for_manifest(manifest: SkillManifest) -> str:
    if manifest.category == "pure":
        return "Phase 1"
    if manifest.category == "hybrid":
        return "Phase 2"
    return "Phase 3"


def _print_report(report: VerificationReport) -> None:
    print("# Skills Architecture Verification")
    print()
    print("## Skill Checks")
    print("| Skill | Category | Phase | frontmatter | body | tools | profile | references | status |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in report.skill_checks:
        print(
            f"| `{item.name}` | `{item.category}` | `{item.phase}` | "
            f"{_tick(item.frontmatter_ok)} | {_tick(item.body_ok)} | {_tick(item.tools_ok)} | "
            f"{_tick(item.profile_ok)} | {_tick(item.references_ok)} | {item.status} |"
        )

    print()
    print("## Pipeline Injection Checks")
    print("| Skill | Message | matched | injected | tool_present | candidates |")
    print("| --- | --- | --- | --- | --- | --- |")
    for item in report.pipeline_checks:
        print(
            f"| `{item.skill_name}` | `{item.message}` | {_tick(item.matched)} | "
            f"{_tick(item.injected)} | {_tick(item.tool_present)} | "
            f"`{', '.join(item.candidates)}` |"
        )

    print()
    print("## Trigger Conflicts")
    if not report.conflicts:
        print("No exact trigger overlaps detected.")
    else:
        print("| Trigger | Skills | Severity | Note |")
        print("| --- | --- | --- | --- |")
        for item in report.conflicts:
            print(
                f"| `{item.trigger}` | `{', '.join(item.skills)}` | `{item.severity}` | {item.note} |"
            )

    print()
    print("## Acceptance Gates")
    acceptance_summary = report.acceptance_report.get("summary", {})
    print("| Gate | total | defined | optional | missing |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for gate in ("unit", "contract", "test_mode_probe", "optional_integration"):
        item = acceptance_summary.get(gate, {})
        print(
            f"| `{gate}` | {int(item.get('total') or 0)} | {int(item.get('defined') or 0)} | "
            f"{int(item.get('optional') or 0)} | {int(item.get('missing') or 0)} |"
        )

    print()
    summary = {
        "skills_total": len(report.skill_checks),
        "pure": sum(1 for item in report.skill_checks if item.category == "pure"),
        "hybrid": sum(1 for item in report.skill_checks if item.category == "hybrid"),
        "internal": sum(1 for item in report.skill_checks if item.category == "internal"),
        "registered_tools": len(report.registered_tools),
        "ok": report.ok,
    }
    print("## Summary")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _tick(value: bool) -> str:
    return "✅" if value else "❌"


def main() -> int:
    report = verify_repo(REPO_ROOT)
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
