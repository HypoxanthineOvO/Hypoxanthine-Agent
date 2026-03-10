from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import parsedate_to_datetime
import imaplib
import json
from pathlib import Path
import secrets
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
        attachments_root: Path | str = "memory/email_attachments",
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.message_queue = message_queue
        self.rules_path = Path(rules_path)
        self.secrets_path = Path(secrets_path)
        self.security_config_path = Path(security_config_path)
        self.imap_client_factory = imap_client_factory or imaplib.IMAP4_SSL
        self.attachments_root = Path(attachments_root)
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
                "summary": str(email_payload.get("subject") or ""),
            }

        # Layer 2: lightweight classification for unmatched or non-skip rules.
        layer2 = await self._layer2_classify(email_payload, layer1)
        category = str(layer2.get("category") or layer1["category"] or "low_priority")
        if category not in {"important", "system", "low_priority", "archive"}:
            category = "low_priority"

        # Layer 3: strong model summary for high-priority categories.
        summary = str(email_payload.get("subject") or "").strip() or "(no subject)"
        if category in {"important", "system"}:
            generated_summary = await self._layer3_summarize(email_payload)
            if generated_summary:
                summary = generated_summary

        return {
            "layer": "llm",
            "category": category,
            "skip_llm": False,
            "reason": str(layer2.get("reason") or f"matched={layer1['matched']}"),
            "confidence": layer2.get("confidence"),
            "summary": summary,
        }

    async def _layer2_classify(
        self,
        email_payload: dict[str, Any],
        layer1: dict[str, Any],
    ) -> dict[str, Any]:
        fallback_category = str(layer1.get("category") or "low_priority")
        if not (
            self.model_router is not None
            and hasattr(self.model_router, "call_lightweight_json")
        ):
            return {
                "category": fallback_category,
                "confidence": None,
                "reason": "router_unavailable",
            }

        prompt = (
            "你是邮件分类器。只输出 JSON，字段包含 category/confidence/reason。"
            "category 只能是 important/system/low_priority/archive。"
            f"\nfrom={email_payload.get('from', '')}"
            f"\nsubject={email_payload.get('subject', '')}"
            f"\nbody={str(email_payload.get('body', ''))[:600]}"
        )
        try:
            result = await self.model_router.call_lightweight_json(prompt)
        except TypeError:
            result = await self.model_router.call_lightweight_json(
                prompt,
                session_id="main",
            )
        except Exception:
            return {
                "category": fallback_category,
                "confidence": None,
                "reason": "layer2_failed",
            }
        if not isinstance(result, dict):
            return {
                "category": fallback_category,
                "confidence": None,
                "reason": "layer2_invalid_payload",
            }
        return {
            "category": str(result.get("category") or fallback_category),
            "confidence": result.get("confidence"),
            "reason": str(result.get("reason") or ""),
        }

    async def _layer3_summarize(self, email_payload: dict[str, Any]) -> str:
        if not (self.model_router is not None and hasattr(self.model_router, "call")):
            return ""
        prompt = (
            "请用中文输出 1-2 句邮件摘要，突出行动项。"
            f"\n发件人: {email_payload.get('from', '')}"
            f"\n标题: {email_payload.get('subject', '')}"
            f"\n正文: {str(email_payload.get('body', ''))[:1000]}"
        )
        messages = [{"role": "user", "content": prompt}]
        model_name = "chat"
        if hasattr(self.model_router, "get_model_for_task"):
            try:
                model_name = str(self.model_router.get_model_for_task("chat") or "chat")
            except Exception:
                model_name = "chat"

        try:
            return str(
                await self.model_router.call(
                    model_name,
                    messages,
                    session_id="main",
                )
            ).strip()
        except Exception:
            return ""

    async def scan_emails(self, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if self._parse_bool(params.get("bootstrap_confirm")):
            draft_id = str(params.get("draft_id") or "").strip()
            if draft_id and draft_id in self._bootstrap_drafts:
                yaml_content = self._bootstrap_drafts[draft_id]
                self._write_rules_atomically(yaml_content)
                self._rules = self._load_rules()
                return {
                    "bootstrap_confirmed": True,
                    "draft_id": draft_id,
                    "summary": "✅ 已写入 email_rules.yaml",
                }
            return {
                "bootstrap_confirmed": False,
                "summary": "❌ draft_id 无效或已过期",
            }

        if not self._rules:
            draft = await self.bootstrap_rules()
            return {
                "requires_confirmation": True,
                "draft_id": draft["draft_id"],
                "yaml_content": draft["yaml_content"],
                "summary": "📂 未检测到有效规则，已生成草稿，确认后写入。",
            }

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

    async def _check_new_emails(self) -> dict[str, Any]:
        # Lightweight event source used by HeartbeatService.
        accounts = self._load_accounts()
        return {"name": "email", "new_items": 0, "accounts": len(accounts)}

    async def bootstrap_rules(self) -> dict[str, Any]:
        yaml_content = (
            "rules:\n"
            "  - name: system-notice\n"
            "    from: \"noreply@\"\n"
            "    category: system\n"
            "    skip_llm: true\n"
            "  - name: boss-important\n"
            "    from: \"boss@\"\n"
            "    category: important\n"
            "    skip_llm: true\n"
            "  - name: newsletter-archive\n"
            "    subject_contains: \"newsletter\"\n"
            "    category: archive\n"
            "    skip_llm: true\n"
        )
        if self.model_router is not None and hasattr(self.model_router, "call_lightweight_json"):
            prompt = (
                "请根据常见邮件场景生成 email rules YAML 草稿，仅返回 YAML，"
                "包含 rules 列表，每条规则支持 name/from/subject_contains/category/skip_llm。"
            )
            try:
                payload = await self.model_router.call_lightweight_json(prompt, session_id="main")
            except TypeError:
                payload = await self.model_router.call_lightweight_json(prompt)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                candidate_yaml = str(payload.get("yaml") or "").strip()
                if candidate_yaml and "rules" in candidate_yaml:
                    yaml_content = candidate_yaml

        draft_id = f"draft_{int(datetime.now(UTC).timestamp())}_{secrets.token_hex(3)}"
        self._bootstrap_drafts[draft_id] = yaml_content
        return {"draft_id": draft_id, "yaml_content": yaml_content}

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
            attachment_paths = self._save_attachments(
                parsed,
                account_name=account_name,
                received_at=payload.get("received_at"),
            )
            item = {
                "account_name": account_name,
                "message_id": payload["message_id"],
                "from": payload["from"],
                "subject": payload["subject"],
                "category": category,
                "summary": str(classification.get("summary") or payload["subject"] or "(no subject)"),
                "attachment_paths": attachment_paths,
            }
            inserted = await self.structured_store.insert_processed_email(
                account_name=account_name,
                message_id=payload["message_id"],
                subject=payload["subject"],
                sender=payload["from"],
                received_at=payload.get("received_at"),
                category=category,
                summary=item["summary"],
                attachment_paths=attachment_paths,
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

    def _save_attachments(
        self,
        parsed: EmailMessage,
        *,
        account_name: str,
        received_at: str | None,
    ) -> list[str]:
        saved: list[str] = []
        bucket = self._attachment_date_bucket(received_at)
        for part in parsed.walk():
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, (bytes, bytearray)):
                continue

            safe_name = self._sanitize_filename(str(make_header(decode_header(filename))))
            target_dir = self.attachments_root / account_name / bucket
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / safe_name
            target.write_bytes(bytes(payload))
            saved.append(str(target))
        return saved

    def _attachment_date_bucket(self, received_at: str | None) -> str:
        if received_at:
            try:
                parsed = parsedate_to_datetime(received_at)
                return parsed.astimezone(UTC).date().isoformat()
            except Exception:
                pass
        return datetime.now(UTC).date().isoformat()

    def _sanitize_filename(self, filename: str) -> str:
        cleaned = filename.strip().replace("\\", "_").replace("/", "_")
        cleaned = "".join(ch for ch in cleaned if ch not in {"\0", ":"})
        return cleaned or "attachment.bin"

    def _parse_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    def _write_rules_atomically(self, yaml_content: str) -> None:
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.rules_path.with_suffix(".tmp")
        tmp.write_text(yaml_content, encoding="utf-8")
        tmp.replace(self.rules_path)
