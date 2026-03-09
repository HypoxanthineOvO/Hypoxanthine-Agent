from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


@dataclass(slots=True)
class EmailRule:
    name: str
    from_substring: str | None = None
    subject_contains: str | None = None
    category: str = "low_priority"
    skip_llm: bool = False


class EmailScannerSkill(BaseSkill):
    name = "email_scanner"
    description = "Scan emails from IMAP accounts and generate summaries."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        structured_store: Any,
        model_router: Any | None,
        message_queue: Any | None,
        rules_path: Path | str = "config/email_rules.yaml",
        security_config_path: Path | str = "config/security.yaml",
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.message_queue = message_queue
        self.rules_path = Path(rules_path)
        self.security_config_path = Path(security_config_path)
        self._rules: list[EmailRule] = self._load_rules()
        self._bootstrap_drafts: dict[str, str] = {}

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "scan_emails",
                    "description": "Scan configured mailboxes and summarize new emails.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bootstrap_confirm": {"type": "boolean", "default": False},
                            "draft_id": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_emails",
                    "description": "Search processed emails by keyword.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_email_detail",
                    "description": "Get processed email detail by message id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"message_id": {"type": "string"}},
                        "required": ["message_id"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "scan_emails":
            result = await self.scan_emails(params=params)
            return SkillOutput(status="success", result=result)
        if tool_name == "search_emails":
            return SkillOutput(
                status="success",
                result={"items": [], "query": str(params.get("query") or "")},
            )
        if tool_name == "get_email_detail":
            return SkillOutput(
                status="success",
                result={"message_id": str(params.get("message_id") or ""), "detail": None},
            )
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    def _load_rules(self) -> list[EmailRule]:
        if not self.rules_path.exists():
            return []
        payload = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            return []
        raw_rules = payload.get("rules", [])
        if not isinstance(raw_rules, list):
            return []

        rules: list[EmailRule] = []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            rules.append(
                EmailRule(
                    name=str(item.get("name") or "unnamed-rule"),
                    from_substring=(
                        str(item.get("from")).strip()
                        if item.get("from") is not None
                        else None
                    ),
                    subject_contains=(
                        str(item.get("subject_contains")).strip()
                        if item.get("subject_contains") is not None
                        else None
                    ),
                    category=str(item.get("category") or "low_priority"),
                    skip_llm=bool(item.get("skip_llm", False)),
                )
            )
        return rules

    def _apply_layer1_rules(self, email_payload: dict[str, Any]) -> dict[str, Any]:
        sender = str(email_payload.get("from") or "")
        subject = str(email_payload.get("subject") or "")
        for rule in self._rules:
            if rule.from_substring and rule.from_substring not in sender:
                continue
            if rule.subject_contains and rule.subject_contains not in subject:
                continue
            return {
                "matched": True,
                "rule_name": rule.name,
                "category": rule.category,
                "skip_llm": rule.skip_llm,
            }
        return {
            "matched": False,
            "rule_name": "",
            "category": "low_priority",
            "skip_llm": False,
        }

    async def _classify_email(self, email_payload: dict[str, Any]) -> dict[str, Any]:
        layer1 = self._apply_layer1_rules(email_payload)
        if layer1["matched"] and layer1["skip_llm"]:
            return {
                "layer": "rule",
                "category": layer1["category"],
                "skip_llm": True,
                "reason": f"matched rule {layer1['rule_name']}",
            }
        return {
            "layer": "rule_fallback",
            "category": layer1["category"],
            "skip_llm": False,
            "reason": f"matched={layer1['matched']}",
        }

    async def scan_emails(self, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        return {
            "accounts_scanned": 0,
            "new_emails": 0,
            "items": [],
            "summary": "📧 邮件扫描完成：暂无新增邮件",
        }

    async def scheduled_scan(self) -> dict[str, Any]:
        result = await self.scan_emails(params={})
        if self.message_queue is not None:
            await self.message_queue.put(
                {
                    "event_type": "email_scan_trigger",
                    "session_id": "main",
                    "summary": str(result.get("summary") or "📧 邮件扫描完成"),
                    "details": json.dumps(result, ensure_ascii=False),
                }
            )
        return result
