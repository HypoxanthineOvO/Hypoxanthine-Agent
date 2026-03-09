from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
import imaplib
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
        secrets_path: Path | str = "config/secrets.yaml",
        security_config_path: Path | str = "config/security.yaml",
        imap_client_factory: Any | None = None,
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.message_queue = message_queue
        self.rules_path = Path(rules_path)
        self.secrets_path = Path(secrets_path)
        self.security_config_path = Path(security_config_path)
        self.imap_client_factory = imap_client_factory or imaplib.IMAP4_SSL
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
        accounts = self._load_accounts()
        accounts_scanned = 0
        accounts_failed = 0
        new_emails = 0
        duplicate_emails = 0
        items: list[dict[str, Any]] = []

        for account in accounts:
            try:
                scanned_items, scanned_duplicates = await self._scan_single_account(account)
            except Exception as exc:
                accounts_failed += 1
                items.append(
                    {
                        "account_name": account.get("name", ""),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            accounts_scanned += 1
            new_emails += len(scanned_items)
            duplicate_emails += scanned_duplicates
            items.extend(scanned_items)

        category_counts = {"important": 0, "low_priority": 0, "archive": 0, "system": 0}
        for item in items:
            category = str(item.get("category") or "")
            if category in category_counts:
                category_counts[category] += 1

        summary = (
            "📧 邮件扫描完成："
            f"🔴 {category_counts['important']} 封重要；"
            f"⚪ {category_counts['low_priority']} 封普通；"
            f"📂 {category_counts['archive']} 封归档"
        )
        return {
            "accounts_scanned": accounts_scanned,
            "accounts_failed": accounts_failed,
            "new_emails": new_emails,
            "duplicate_emails": duplicate_emails,
            "items": items,
            "summary": summary,
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

    def _load_accounts(self) -> list[dict[str, Any]]:
        if not self.secrets_path.exists():
            return []
        payload = yaml.safe_load(self.secrets_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            return []
        services = payload.get("services")
        if not isinstance(services, dict):
            return []
        email_cfg = services.get("email")
        if not isinstance(email_cfg, dict):
            return []
        accounts = email_cfg.get("accounts")
        if not isinstance(accounts, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "name": str(item.get("name") or "default"),
                    "host": str(item.get("host") or ""),
                    "port": int(item.get("port") or 993),
                    "username": str(item.get("username") or ""),
                    "password": str(item.get("password") or ""),
                    "folder": str(item.get("folder") or "INBOX"),
                }
            )
        return normalized

    async def _scan_single_account(self, account: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        host = str(account.get("host") or "")
        port = int(account.get("port") or 993)
        if not host:
            raise ValueError("email account host is required")

        client = self.imap_client_factory(host, port)
        username = str(account.get("username") or "")
        password = str(account.get("password") or "")
        folder = str(account.get("folder") or "INBOX")
        account_name = str(account.get("name") or username or host)
        client.login(username, password)
        client.select(folder)

        status, search_data = client.search(None, "UNSEEN")
        if status != "OK":
            client.logout()
            raise RuntimeError(f"imap search failed for account={account_name}")

        ids_blob = search_data[0] if search_data else b""
        msg_ids = [item for item in ids_blob.split() if item]
        processed_items: list[dict[str, Any]] = []
        duplicate_count = 0

        for msg_num in msg_ids:
            fetch_status, fetched = client.fetch(msg_num, "(RFC822)")
            if fetch_status != "OK" or not fetched:
                continue
            raw_bytes = fetched[0][1] if isinstance(fetched[0], tuple) else None
            if not isinstance(raw_bytes, (bytes, bytearray)):
                continue
            parsed = message_from_bytes(raw_bytes)
            payload = self._extract_email_payload(parsed, account_name=account_name, msg_num=msg_num)

            if await self.structured_store.has_processed_email(account_name, payload["message_id"]):
                duplicate_count += 1
                continue

            classification = await self._classify_email(payload)
            category = str(classification.get("category") or "low_priority")
            item = {
                "account_name": account_name,
                "message_id": payload["message_id"],
                "from": payload["from"],
                "subject": payload["subject"],
                "category": category,
                "summary": str(classification.get("summary") or payload["subject"] or "(no subject)"),
            }
            inserted = await self.structured_store.insert_processed_email(
                account_name=account_name,
                message_id=payload["message_id"],
                subject=payload["subject"],
                sender=payload["from"],
                received_at=payload.get("received_at"),
                category=category,
                summary=item["summary"],
                attachment_paths=[],
            )
            if inserted:
                processed_items.append(item)

            # Mark as read only after successful processing.
            client.store(msg_num, "+FLAGS", "(\\Seen)")

        client.logout()
        return processed_items, duplicate_count

    def _extract_email_payload(
        self,
        parsed: EmailMessage,
        *,
        account_name: str,
        msg_num: bytes,
    ) -> dict[str, str]:
        raw_subject = str(parsed.get("Subject") or "")
        subject = str(make_header(decode_header(raw_subject))) if raw_subject else ""
        sender = str(parsed.get("From") or "")
        message_id = str(parsed.get("Message-ID") or "").strip()
        if not message_id:
            message_id = f"<{account_name}-{msg_num.decode('utf-8', errors='ignore')}>"
        received_at = str(parsed.get("Date") or "")
        body = self._extract_text_body(parsed)
        return {
            "message_id": message_id,
            "from": sender,
            "subject": subject,
            "received_at": received_at,
            "body": body,
        }

    def _extract_text_body(self, parsed: EmailMessage) -> str:
        if parsed.is_multipart():
            for part in parsed.walk():
                content_type = str(part.get_content_type() or "").lower()
                disposition = str(part.get("Content-Disposition") or "").lower()
                if content_type == "text/plain" and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, (bytes, bytearray)):
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, (bytes, bytearray)):
            return payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
        if isinstance(payload, str):
            return payload
        return ""
