from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


Dimension = dict[str, Any]


def _read(repo_root: Path, relative_path: str) -> str:
    path = repo_root / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _exists(repo_root: Path, relative_path: str) -> bool:
    return (repo_root / relative_path).exists()


def _finding(finding_id: str, severity: str, summary: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
    }


def _dimension(findings: list[dict[str, Any]]) -> Dimension:
    return {
        "findings": findings,
        "finding_count": len(findings),
        "critical_count": sum(1 for item in findings if item["severity"] == "Critical"),
        "warning_count": sum(1 for item in findings if item["severity"] == "Warning"),
        "info_count": sum(1 for item in findings if item["severity"] == "Info"),
    }


def _scan_resource_reference(repo_root: Path) -> Dimension:
    models = _read(repo_root, "src/hypo_agent/models.py")
    fs_skill = _read(repo_root, "src/hypo_agent/skills/fs_skill.py")
    findings: list[dict[str, Any]] = []

    if "class Attachment" in models and "class ResourceRef" not in models:
        findings.append(
            _finding(
                "resource_ref_missing_first_class_model",
                "Critical",
                "Resource identity is represented as loose attachment/path/url fields, not as a first-class resolvable resource.",
                [
                    "models.py defines Attachment(url, filename, mime_type) and deprecated Message.file/image/audio.",
                    "No ResourceRef or ResourceResolver model is present in the backend package.",
                ],
            )
        )

    if "_resolve_read_target" in fs_skill and "candidate" in fs_skill and "File not found" in fs_skill:
        findings.append(
            _finding(
                "filesystem_missing_candidate_search",
                "Critical",
                "The filesystem tool tries a few fixed paths but does not return ranked candidates or ask for disambiguation.",
                [
                    "FileSystemSkill._resolve_read_target picks the first existing candidate or the first guessed path.",
                    "read_file returns 'File not found' instead of a candidate list or recovery action.",
                ],
            )
        )

    return _dimension(findings)


def _scan_tool_recovery(repo_root: Path) -> Dimension:
    pipeline = _read(repo_root, "src/hypo_agent/core/pipeline.py")
    skill_manager = _read(repo_root, "src/hypo_agent/core/skill_manager.py")
    tool_outcome = _read(repo_root, "src/hypo_agent/core/tool_outcome.py")
    findings: list[dict[str, Any]] = []

    if '"user_input_error"' in tool_outcome and "retryable=False" in tool_outcome:
        findings.append(
            _finding(
                "tool_user_input_error_marked_non_retryable",
                "Critical",
                "Missing parameters and not-found resource errors are classified as non-retryable user input errors.",
                [
                    "tool_outcome.py maps not found / required / missing to user_input_error with retryable=False.",
                    "This prevents a unified search/ask/retry recovery loop from being a first-class tool outcome.",
                ],
            )
        )

    if "invoke(" in skill_manager and "params: dict[str, Any]" in skill_manager and "jsonschema" not in skill_manager:
        findings.append(
            _finding(
                "tool_preinvoke_schema_validation_missing",
                "Warning",
                "SkillManager receives raw params but does not perform centralized JSON-schema validation before dispatch.",
                [
                    "SkillManager.invoke accepts params as dict[str, Any].",
                    "There is no central validator that returns structured missing/invalid field recovery hints.",
                ],
            )
        )

    if "search/ask/retry" not in pipeline and "_RETRYABLE_TOOLS" in pipeline:
        findings.append(
            _finding(
                "agent_recovery_state_machine_missing",
                "Warning",
                "Tool recovery is encoded as scattered retryable tool lists and prompts, not an explicit agent state machine.",
                [
                    "pipeline.py has _RETRYABLE_TOOLS and prompt instructions.",
                    "No shared search/ask/retry/fallback/verify state object is present.",
                ],
            )
        )

    return _dimension(findings)


def _scan_webpage_reading(repo_root: Path) -> Dimension:
    web_skill = _read(repo_root, "src/hypo_agent/skills/agent_search_skill.py")
    findings: list[dict[str, Any]] = []

    if "client.extract" in web_skill and "playwright" not in web_skill.lower():
        findings.append(
            _finding(
                "web_read_browser_fallback_missing",
                "Critical",
                "Web reading depends on Tavily extract plus hand-coded Zhihu API fallback, with no browser-rendered fallback.",
                [
                    "AgentSearchSkill.web_read calls Tavily client.extract first.",
                    "Zhihu support is URL-pattern API fetching, not simulated browser rendering.",
                    "No Playwright/browser session layer appears in agent_search_skill.py.",
                ],
            )
        )

    if "Missing Tavily API key" in web_skill:
        findings.append(
            _finding(
                "web_read_single_provider_hard_dependency",
                "Warning",
                "The primary webpage reader has a hard dependency on Tavily credentials before fallback decisions.",
                [
                    "AgentSearchSkill._load_api_key raises when services.tavily.api_key is missing.",
                    "Fallback strategy is inside the same skill rather than a provider-agnostic webpage reader service.",
                ],
            )
        )

    return _dimension(findings)


def _scan_channel_file_delivery(repo_root: Path) -> Dimension:
    delivery = _read(repo_root, "src/hypo_agent/core/delivery.py")
    feishu = _read(repo_root, "src/hypo_agent/channels/feishu_channel.py")
    qq_bot = _read(repo_root, "src/hypo_agent/channels/qq_bot_channel.py")
    weixin_adapter = _read(repo_root, "src/hypo_agent/channels/weixin/weixin_adapter.py")
    dispatcher = _read(repo_root, "src/hypo_agent/core/channel_dispatcher.py")
    findings: list[dict[str, Any]] = []

    channel_supports_files = all(
        token in text
        for token, text in (
            ("upload_file", feishu),
            ("_send_file_with_fallback", qq_bot),
            ("_build_file_item", weixin_adapter),
        )
    )
    if channel_supports_files and "Capability" not in delivery:
        findings.append(
            _finding(
                "channel_attachment_capability_contract_missing",
                "Critical",
                "Channels have file-send primitives, but delivery results do not expose a unified attachment capability contract.",
                [
                    "Feishu, QQ Bot, and Weixin code contain file upload/send primitives.",
                    "core.delivery.DeliveryResult only reports success/error counts.",
                    "ChannelRelayPolicy routes messages without checking attachment capabilities or surfacing recovery actions.",
                ],
            )
        )

    if "non_main_session" in dispatcher:
        findings.append(
            _finding(
                "external_channel_relay_session_policy_hidden",
                "Warning",
                "External channel relay silently skips non-main sessions, which can hide file delivery failures from users.",
                [
                    "ChannelRelayPolicy skips external registrations when message.session_id != 'main'.",
                    "This policy is logged but not modeled as a user-visible delivery capability decision.",
                ],
            )
        )

    return _dimension(findings)


def _scan_frontend_observability(repo_root: Path) -> Dimension:
    message_types = _read(repo_root, "web/src/types/message.ts")
    message_routing = _read(repo_root, "web/src/utils/messageRouting.ts")
    components = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (repo_root / "web/src/components/chat").glob("*.vue")
    ) if _exists(repo_root, "web/src/components/chat") else ""
    findings: list[dict[str, Any]] = []

    if "tool_call_start" in message_types and "candidate" not in message_types.lower():
        findings.append(
            _finding(
                "frontend_missing_resource_candidate_confirmation",
                "Critical",
                "The WebUI can render tool states and errors, but has no typed resource-candidate confirmation event.",
                [
                    "web/src/types/message.ts defines tool_call_start/result/error shapes.",
                    "No resource candidate, disambiguation, or confirmation event type is declared.",
                ],
            )
        )

    if "retryable" in components and "tool_call_error" in message_types:
        findings.append(
            _finding(
                "frontend_error_retry_is_message_level_not_operation_level",
                "Warning",
                "Frontend retry affordance is message-level, not tied to structured recovery actions from a failed operation.",
                [
                    "ErrorStateCard exposes retryable retry UI.",
                    "ToolCallMessage and PipelineProgress show status but not recovery choices such as search candidates or fallback path.",
                ],
            )
        )

    if "attachments" in message_routing and "candidate" not in message_routing.lower():
        findings.append(
            _finding(
                "frontend_attachment_display_not_resource_resolution",
                "Info",
                "Frontend attachments are displayed as media/files, but not connected to a shared resource-resolution lifecycle.",
                [
                    "messageRouting.ts detects attachments and file previews.",
                    "There is no candidate confirmation path for ambiguous file references.",
                ],
            )
        )

    return _dimension(findings)


def scan_repo(repo_root: Path | str) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=False)
    dimensions = {
        "resource_reference": _scan_resource_reference(root),
        "tool_recovery": _scan_tool_recovery(root),
        "webpage_reading": _scan_webpage_reading(root),
        "channel_file_delivery": _scan_channel_file_delivery(root),
        "frontend_observability": _scan_frontend_observability(root),
    }
    findings_total = sum(item["finding_count"] for item in dimensions.values())
    return {
        "repo_root": str(root),
        "summary": {
            "dimensions": len(dimensions),
            "findings_total": findings_total,
            "critical_total": sum(item["critical_count"] for item in dimensions.values()),
            "warning_total": sum(item["warning_count"] for item in dimensions.values()),
            "info_total": sum(item["info_count"] for item in dimensions.values()),
        },
        "dimensions": dimensions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit first-principles system contracts.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    report = scan_repo(Path(args.repo_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    summary = report["summary"]
    print(
        "System contract audit: "
        f"{summary['findings_total']} findings across {summary['dimensions']} dimensions "
        f"({summary['critical_total']} critical, {summary['warning_total']} warning, {summary['info_total']} info)."
    )
    for dimension_name, dimension in report["dimensions"].items():
        print(f"\n[{dimension_name}]")
        for finding in dimension["findings"]:
            print(f"- {finding['severity']} {finding['id']}: {finding['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
