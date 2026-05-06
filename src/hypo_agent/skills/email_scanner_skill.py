from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import parsedate_to_datetime
import html
import imaplib
import json
from pathlib import Path
import secrets
import re
from typing import Any

import structlog
import yaml

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.exceptions import ModelError
from hypo_agent.memory.email_store import EmailStore
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill
from hypo_agent.utils.timeutil import localize_iso, now_iso, to_local

logger = structlog.get_logger("hypo_agent.skills.email_scanner_skill")

_EMAIL_MODEL_ERRORS = (
    ModelError,
    asyncio.TimeoutError,
    TimeoutError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
_EMAIL_IMAP_ERRORS = (imaplib.IMAP4.error, OSError, RuntimeError, TypeError, ValueError)
_EMAIL_DATE_ERRORS = (TypeError, ValueError, OverflowError)


def _error_fields(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    if len(message) > 200:
        message = f"{message[:197]}..."
    return {
        "error_type": type(exc).__name__,
        "error_msg": message,
    }


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
    HEARTBEAT_SCAN_STARTED_AT_PREF_KEY = "email_scanner.last_heartbeat_scan_started_at"
    HEARTBEAT_LAYER2_TIMEOUT_SECONDS = 8.0

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
        attachments_root: Path | str | None = None,
        email_store: EmailStore | None = None,
        mark_as_read: bool = True,
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.message_queue = message_queue
        self.rules_path = Path(rules_path)
        self.secrets_path = Path(secrets_path)
        self.security_config_path = Path(security_config_path)
        self.imap_client_factory = imap_client_factory or imaplib.IMAP4_SSL

        if attachments_root is None:
            attachments_root = get_memory_dir() / "email_attachments"
        self.attachments_root = Path(attachments_root)
        self.email_store = email_store or EmailStore(root=get_memory_dir() / "emails")
        self.mark_as_read = bool(mark_as_read)
        self._rules, self._user_preferences = self._load_rule_config()
        self._bootstrap_drafts: dict[str, str] = {}
        self.last_scan_at: str | None = None
        self.emails_processed = 0
        self._scan_in_progress = False
        self._last_heartbeat_scan_started_at: datetime | None = None
        self._recent_reported_message_ids: deque[str] = deque()
        self._recent_reported_message_id_set: set[str] = set()

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "scan_emails",
                    "description": (
                        "扫描邮箱收件箱，拉取最近一段时间的邮件并按用户偏好分类和摘要。"
                        "默认查看最近 24 小时。用户说'看邮件'、'查邮件'、"
                        "'有什么新邮件'、'帮我看最近三天的邮件'时调用此工具。"
                        "扫描结果会自动缓存，后续搜索会更快更准。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bootstrap_confirm": {"type": "boolean", "default": False},
                            "draft_id": {"type": "string"},
                            "hours_back": {
                                "type": "integer",
                                "default": 24,
                                "minimum": 1,
                            },
                            "unread_only": {
                                "type": "boolean",
                                "default": False,
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_emails",
                    "description": (
                        "搜索邮件。优先从本地缓存搜索，缓存不足时自动从邮箱补充。"
                        "如果搜索结果不理想，你应该："
                        "1. 换不同的关键词重试（比如用发件人名字、邮件主题的部分词）；"
                        "2. 用 list_emails 拉最近几天的邮件列表，自己浏览判断；"
                        "3. 扩大时间范围（hours_back 参数）；"
                        "4. 反问用户：你记得大概是什么时候的邮件？发件人是谁？"
                        "不要在第一次搜索失败后就告诉用户搜不到。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "hours_back": {
                                "type": "integer",
                                "default": 168,
                                "minimum": 1,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_emails",
                    "description": (
                        "列出最近的邮件概览（标题、发件人、时间）。"
                        "这是你的邮件数据源。当用户说帮我找某封邮件时，你可以："
                        "1. 先用 list_emails 拉最近几天的列表；"
                        "2. 自己根据用户描述判断哪些邮件可能相关；"
                        "3. 用 get_email_detail 展开看正文确认；"
                        "4. 如果列表里没找到，扩大 hours_back 或反问用户更多线索。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hours_back": {
                                "type": "integer",
                                "default": 72,
                                "minimum": 1,
                            },
                            "limit": {
                                "type": "integer",
                                "default": 30,
                                "minimum": 1,
                            },
                            "from_filter": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_email_detail",
                    "description": (
                        "获取某封邮件的完整内容。用户说'看一下这封邮件的详情'、"
                        "'展开这封邮件'时调用此工具。参数：message_id（从 scan 或 search 结果中获取）。"
                        "获取的正文会缓存到本地，下次可以直接搜索到正文内容。"
                    ),
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
            result = await self.search_emails(
                query=str(params.get("query") or ""),
                hours_back=params.get("hours_back", 168),
            )
            return SkillOutput(status="success", result=result)
        if tool_name == "list_emails":
            result = await self.list_emails(
                hours_back=params.get("hours_back", 72),
                limit=params.get("limit", 30),
                from_filter=params.get("from_filter"),
            )
            return SkillOutput(status="success", result=result)
        if tool_name == "get_email_detail":
            result = await self.get_email_detail(message_id=str(params.get("message_id") or ""))
            return SkillOutput(status="success", result=result)
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    def _load_rule_config(self) -> tuple[list[EmailRule], str]:
        if not self.rules_path.exists():
            return [], ""
        payload = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            return [], ""
        raw_rules = payload.get("rules", [])
        raw_preferences = payload.get("user_preferences", "")
        if not isinstance(raw_rules, list):
            raw_rules = []

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
        preferences = ""
        if isinstance(raw_preferences, str):
            preferences = raw_preferences.strip()
        elif isinstance(raw_preferences, list):
            preferences = "\n".join(str(item).strip() for item in raw_preferences if str(item).strip())
        return rules, preferences

    def _reload_rule_config(self) -> None:
        self._rules, self._user_preferences = self._load_rule_config()

    def configure_email_store(self, *, max_entries: int, retention_days: int) -> None:
        self.email_store.configure(
            max_entries=max_entries,
            retention_days=retention_days,
        )

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

    async def _classify_email(
        self,
        email_payload: dict[str, Any],
        *,
        triggered_by: str = "",
    ) -> dict[str, Any]:
        layer1 = self._apply_layer1_rules(email_payload)
        heartbeat_mode = str(triggered_by or "").strip().lower() == "heartbeat"
        if layer1["matched"] and layer1["skip_llm"]:
            return {
                "layer": "rule",
                "category": layer1["category"],
                "skip_llm": True,
                "reason": f"matched rule {layer1['rule_name']}",
                "summary": str(email_payload.get("subject") or ""),
            }

        # Layer 2: lightweight classification for unmatched or non-skip rules.
        layer2_timeout = self.HEARTBEAT_LAYER2_TIMEOUT_SECONDS if heartbeat_mode else None
        layer2 = await self._layer2_classify(
            email_payload,
            layer1,
            timeout_seconds=layer2_timeout,
        )
        category = str(layer2.get("category") or layer1["category"] or "low_priority")
        if category not in {"important", "system", "low_priority", "archive"}:
            category = "low_priority"

        # Keep heartbeat scans cheap: classification is enough, subject remains as summary.
        summary = str(email_payload.get("subject") or "").strip() or "(no subject)"
        if category in {"important", "system"} and not heartbeat_mode:
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
        *,
        timeout_seconds: float | None = None,
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
        )
        if self._user_preferences:
            prompt += f"\n\n{self._user_preferences}"
        prompt += (
            f"\n\nLayer1 分类结果: matched={layer1.get('matched')} "
            f"category={layer1.get('category')} skip_llm={layer1.get('skip_llm')}"
            f"\nfrom={email_payload.get('from', '')}"
            f"\nsubject={email_payload.get('subject', '')}"
            f"\nbody={str(email_payload.get('body', ''))[:600]}"
        )
        try:
            result = await self._await_model_result(
                self.model_router.call_lightweight_json(prompt),
                timeout_seconds=timeout_seconds,
            )
        except TypeError:
            result = await self._await_model_result(
                self.model_router.call_lightweight_json(
                    prompt,
                    session_id="main",
                ),
                timeout_seconds=timeout_seconds,
            )
        except _EMAIL_MODEL_ERRORS as exc:
            # FALLBACK: category falls back to layer1 when the lightweight classifier is unavailable.
            logger.warning(
                "email_scanner.layer2.fallback_used",
                **_error_fields(exc),
            )
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
            "不要复述标题；如果标题已包含的信息足够，只补充正文里的时间、地点、要求或截止日期。"
            f"\n发件人: {email_payload.get('from', '')}"
            f"\n标题: {email_payload.get('subject', '')}"
            f"\n正文: {str(email_payload.get('body', ''))[:1000]}"
        )
        messages = [{"role": "user", "content": prompt}]
        model_name = "chat"
        if hasattr(self.model_router, "get_model_for_task"):
            try:
                model_name = str(self.model_router.get_model_for_task("chat") or "chat")
            except _EMAIL_MODEL_ERRORS as exc:
                # FALLBACK: use the default chat model when routing metadata is unavailable.
                logger.warning(
                    "email_scanner.layer3_model.fallback_used",
                    **_error_fields(exc),
                )
                model_name = "chat"

        try:
            generated = str(
                await self._await_model_result(
                    self.model_router.call(
                        model_name,
                        messages,
                        session_id="main",
                    ),
                    timeout_seconds=None,
                )
            ).strip()
            return self._normalize_generated_summary(
                subject=str(email_payload.get("subject") or ""),
                body=str(email_payload.get("body") or ""),
                summary=generated,
            )
        except _EMAIL_MODEL_ERRORS as exc:
            # FALLBACK: summary text is optional and can be omitted when generation fails.
            logger.warning(
                "email_scanner.layer3_summary.fallback_used",
                **_error_fields(exc),
            )
            return ""

    async def _await_model_result(
        self,
        awaitable: Any,
        *,
        timeout_seconds: float | None,
    ) -> Any:
        if timeout_seconds is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)

    async def scan_emails(self, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self._reload_rule_config()
        self._scan_in_progress = True
        try:
            if self._parse_bool(params.get("bootstrap_confirm")):
                draft_id = str(params.get("draft_id") or "").strip()
                if draft_id and draft_id in self._bootstrap_drafts:
                    yaml_content = self._bootstrap_drafts[draft_id]
                    self._write_rules_atomically(yaml_content)
                    self._reload_rule_config()
                    return {
                        "bootstrap_confirmed": True,
                        "draft_id": draft_id,
                        "summary": "✅ 已写入 email_rules.yaml",
                    }
                return {
                    "bootstrap_confirmed": False,
                    "summary": "❌ draft_id 无效或已过期",
                }

            triggered_by = self._parse_triggered_by(params.get("triggered_by"))
            unread_only = self._parse_bool(params.get("unread_only"))
            hours_back = self._parse_hours_back(params.get("hours_back"))
            scan_started_at = datetime.now(UTC)
            heartbeat_since_dt = await self._load_last_heartbeat_scan_started_at(
                triggered_by=triggered_by,
            )
            cutoff_dt = self._resolve_scan_cutoff(
                triggered_by=triggered_by,
                hours_back=hours_back,
                now=scan_started_at,
                heartbeat_since_dt=heartbeat_since_dt,
            )
            dedupe_message_ids = (
                self._recent_reported_message_id_set
                if triggered_by == "heartbeat"
                else None
            )
            accounts = self._load_accounts()
            accounts_scanned = 0
            accounts_failed = 0
            new_emails = 0
            duplicate_emails = 0
            items: list[dict[str, Any]] = []

            for account in accounts:
                try:
                    scanned_items, scanned_duplicates = await self._scan_single_account(
                        account,
                        cutoff_dt=cutoff_dt,
                        unread_only=unread_only,
                        dedupe_message_ids=dedupe_message_ids,
                        triggered_by=triggered_by,
                    )
                except _EMAIL_IMAP_ERRORS as exc:
                    logger.warning(
                        "email_scanner.scan_account.fallback_used",
                        account_name=str(account.get("name") or ""),
                        **_error_fields(exc),
                    )
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
            self.last_scan_at = now_iso()
            self.emails_processed += new_emails
            if triggered_by == "heartbeat":
                if items:
                    self._remember_reported_message_ids(
                        str(item.get("message_id") or "")
                        for item in items
                    )
                await self._persist_last_heartbeat_scan_started_at(scan_started_at)
            return {
                "accounts_scanned": accounts_scanned,
                "accounts_failed": accounts_failed,
                "new_emails": new_emails,
                "duplicate_emails": duplicate_emails,
                "items": items,
                "summary": summary,
                "triggered_by": triggered_by,
                "hours_back": hours_back,
                "unread_only": unread_only,
            }
        finally:
            self._scan_in_progress = False

    async def scheduled_scan(self) -> dict[str, Any]:
        result = await self.scan_emails(params={"triggered_by": "heartbeat"})
        if self.message_queue is not None and int(result.get("new_emails") or 0) > 0:
            await self.message_queue.put(
                {
                    "event_type": "email_scan_trigger",
                    "session_id": "main",
                    "summary": str(result.get("summary") or "📧 邮件扫描完成"),
                    "details": json.dumps(result, ensure_ascii=False),
                }
            )
        return result

    async def search_emails(
        self,
        *,
        query: str,
        hours_back: Any = 168,
        limit: int = 20,
    ) -> dict[str, Any]:
        cleaned_query = str(query or "").strip()
        parsed_hours_back = self._parse_hours_back(hours_back)
        parsed_limit = self._parse_limit(limit, default=20, maximum=50)
        await self._warm_cache_if_needed(hours_back=parsed_hours_back)

        local_items = [
            self._record_to_overview(item, source="local_cache")
            for item in self.email_store.search_local(
                cleaned_query,
                limit=parsed_limit,
                hours_back=parsed_hours_back,
            )
        ]
        results = list(local_items)
        if len(results) < 3:
            imap_items = await self._imap_subject_search(
                query=cleaned_query,
                hours_back=parsed_hours_back,
                limit=parsed_limit - len(results),
            )
            seen_ids = {str(item.get("message_id") or "") for item in results}
            for item in imap_items:
                message_id = str(item.get("message_id") or "")
                if not message_id or message_id in seen_ids:
                    continue
                results.append(item)
                seen_ids.add(message_id)
                if len(results) >= parsed_limit:
                    break

        return {
            "query": cleaned_query,
            "hours_back": parsed_hours_back,
            "items": results[:parsed_limit],
        }

    async def list_emails(
        self,
        *,
        hours_back: Any = 72,
        limit: Any = 30,
        from_filter: Any = None,
    ) -> dict[str, Any]:
        parsed_hours_back = self._parse_hours_back(hours_back)
        parsed_limit = self._parse_limit(limit, default=30, maximum=100)
        await self._warm_cache_if_needed(hours_back=parsed_hours_back)

        items = [
            self._record_to_overview(item, source="local_cache")
            for item in self.email_store.list_recent(
                hours_back=parsed_hours_back,
                limit=parsed_limit,
                from_filter=str(from_filter or "").strip() or None,
            )
        ]
        return {
            "hours_back": parsed_hours_back,
            "limit": parsed_limit,
            "items": items,
        }

    async def get_email_detail(self, *, message_id: str) -> dict[str, Any]:
        key = str(message_id or "").strip()
        if not key:
            return {"message_id": "", "detail": None}

        metadata = self.email_store.get_metadata(key) or {"message_id": key}
        cached_body = self.email_store.get_body(key)
        if cached_body is not None:
            return {
                "message_id": key,
                "source": "local_cache",
                "detail": {
                    **metadata,
                    "body": cached_body,
                },
            }

        fetched = await self._fetch_email_by_message_id(key)
        if fetched is None:
            return {"message_id": key, "detail": None}

        body = str(fetched.get("body") or "")
        self.email_store.upsert(
            self._build_email_store_record(
                fetched,
                category=str(metadata.get("category") or ""),
                attachment_paths=metadata.get("attachment_paths"),
            )
        )
        self.email_store.upsert_body(key, body)
        merged = self.email_store.get_metadata(key) or metadata
        return {
            "message_id": key,
            "source": "imap",
            "detail": {
                **merged,
                "body": body,
            },
        }

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
            except _EMAIL_MODEL_ERRORS as exc:
                # FALLBACK: keep the built-in bootstrap draft when LLM generation fails.
                logger.warning(
                    "email_scanner.bootstrap_rules.fallback_used",
                    **_error_fields(exc),
                )
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

    async def _scan_single_account(
        self,
        account: dict[str, Any],
        *,
        cutoff_dt: datetime | None,
        unread_only: bool,
        dedupe_message_ids: set[str] | None,
        triggered_by: str,
    ) -> tuple[list[dict[str, Any]], int]:
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
        try:
            try:
                client.select(folder, readonly=not self.mark_as_read)
            except TypeError:
                client.select(folder)

            search_criteria = self._build_search_criteria(
                cutoff_dt=cutoff_dt,
                unread_only=unread_only,
            )
            status, search_data = client.search(None, *search_criteria)
            if status != "OK":
                raise RuntimeError(f"imap search failed for account={account_name}")

            ids_blob = search_data[0] if search_data else b""
            msg_ids = [item for item in ids_blob.split() if item]
            processed_items: list[dict[str, Any]] = []
            duplicate_count = 0

            for msg_num in msg_ids:
                fetch_status, fetched = client.fetch(msg_num, "(BODY.PEEK[])")
                if fetch_status != "OK" or not fetched:
                    continue
                raw_bytes = fetched[0][1] if isinstance(fetched[0], tuple) else None
                if not isinstance(raw_bytes, (bytes, bytearray)):
                    continue
                parsed = message_from_bytes(raw_bytes)
                payload = self._extract_email_payload(parsed, account_name=account_name, msg_num=msg_num)
                received_dt = self._parse_received_datetime(payload.get("received_at"))
                if not self._is_within_cutoff(received_dt, cutoff_dt):
                    continue

                message_id = payload["message_id"]
                if dedupe_message_ids is not None and message_id in dedupe_message_ids:
                    duplicate_count += 1
                    continue

                classification = await self._classify_email(payload, triggered_by=triggered_by)
                category = str(classification.get("category") or "low_priority")
                attachment_paths = self._save_attachments(
                    parsed,
                    account_name=account_name,
                    received_at=payload.get("received_at"),
                )
                item = {
                    "account_name": account_name,
                    "message_id": message_id,
                    "from": payload["from"],
                    "subject": payload["subject"],
                    "received_at": payload.get("received_at"),
                    "category": category,
                    "summary": str(classification.get("summary") or payload["subject"] or "(no subject)"),
                    "attachment_paths": attachment_paths,
                }
                if hasattr(self.structured_store, "insert_processed_email"):
                    await self.structured_store.insert_processed_email(
                        account_name=account_name,
                        message_id=message_id,
                        subject=payload["subject"],
                        sender=payload["from"],
                        received_at=payload.get("received_at"),
                        category=category,
                        summary=item["summary"],
                        attachment_paths=attachment_paths,
                    )
                self.email_store.upsert(
                    self._build_email_store_record(
                        payload,
                        category=category,
                        attachment_paths=attachment_paths,
                        account_name=account_name,
                    )
                )
                self._mark_message_as_read(client, msg_num)
                processed_items.append(item)

            return processed_items, duplicate_count
        finally:
            try:
                client.logout()
            except _EMAIL_IMAP_ERRORS as exc:
                # FALLBACK: logout failure should not invalidate already fetched messages.
                logger.warning(
                    "email_scanner.logout.fallback_used",
                    account_name=account_name,
                    **_error_fields(exc),
                )

    def _extract_email_payload(
        self,
        parsed: EmailMessage,
        *,
        account_name: str,
        msg_num: bytes,
    ) -> dict[str, str]:
        raw_subject = str(parsed.get("Subject") or "")
        subject = self._clean_email_text(str(make_header(decode_header(raw_subject))) if raw_subject else "")
        sender = self._clean_email_text(str(parsed.get("From") or ""))
        recipient = self._clean_email_text(str(parsed.get("To") or ""))
        message_id = str(parsed.get("Message-ID") or "").strip()
        if not message_id:
            message_id = f"<{account_name}-{msg_num.decode('utf-8', errors='ignore')}>"
        received_at = str(parsed.get("Date") or "")
        body = self._clean_email_text(self._extract_text_body(parsed), strip=False)
        return {
            "message_id": message_id,
            "from": sender,
            "to": recipient,
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
                        return self._decode_email_bytes(payload, charset=part.get_content_charset())
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, (bytes, bytearray)):
            return self._decode_email_bytes(payload, charset=parsed.get_content_charset())
        if isinstance(payload, str):
            return payload
        return ""

    def _decode_email_bytes(self, payload: bytes | bytearray, *, charset: str | None) -> str:
        data = bytes(payload)
        candidates = [str(charset or "").strip(), "utf-8", "gb18030", "big5", "cp1252", "latin-1"]
        seen: set[str] = set()
        decoded: list[str] = []
        for candidate in candidates:
            if not candidate or candidate.lower() in seen:
                continue
            seen.add(candidate.lower())
            try:
                decoded.append(data.decode(candidate, errors="strict"))
            except (LookupError, UnicodeDecodeError):
                continue
        if decoded:
            return min((self._clean_email_text(item, strip=False) for item in decoded), key=self._email_text_badness)
        return self._clean_email_text(data.decode("utf-8", errors="replace"), strip=False)

    def _clean_email_text(self, value: str, *, strip: bool = True) -> str:
        text = html.unescape(str(value or "")).replace("\x00", "")
        text = self._repair_mojibake(text)
        text = text.replace("\ufffd", "")
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        return text.strip() if strip else text

    def _repair_mojibake(self, value: str) -> str:
        text = str(value or "")
        repaired_candidates = [text]
        for source_encoding in ("latin-1", "cp1252"):
            try:
                repaired_candidates.append(text.encode(source_encoding).decode("utf-8"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return min(repaired_candidates, key=self._email_text_badness)

    def _email_text_badness(self, value: str) -> int:
        text = str(value or "")
        suspicious = sum(text.count(token) for token in ("�", "Ã", "Â", "â", "æ", "è", "é", "å", "ç"))
        controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\t")
        cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        return suspicious * 12 + controls * 20 - cjk

    def _normalize_generated_summary(self, *, subject: str, body: str, summary: str) -> str:
        del body
        cleaned = self._clean_email_text(summary)
        subject_clean = self._clean_email_text(subject)
        if not cleaned or not subject_clean:
            return cleaned
        escaped_subject = re.escape(subject_clean)
        cleaned = re.sub(rf"^\s*{escaped_subject}\s*[:：,，\-— ]*", "", cleaned).strip()
        cleaned = re.sub(rf"^\s*{escaped_subject}\s+", "", cleaned).strip()
        cleaned = re.sub(rf"([:：,，\-— ]*){escaped_subject}([:：,，\-— ]*)", r"\1", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:,，-—")
        return cleaned or subject_clean

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
            except _EMAIL_DATE_ERRORS as exc:
                # FALLBACK: invalid Date headers use the current UTC date for attachment bucketing.
                logger.debug(
                    "email_scanner.attachment_bucket.fallback_used",
                    received_at=received_at,
                    **_error_fields(exc),
                )
        return datetime.now(UTC).date().isoformat()

    def _sanitize_filename(self, filename: str) -> str:
        cleaned = filename.strip().replace("\\", "_").replace("/", "_")
        cleaned = "".join(ch for ch in cleaned if ch not in {"\0", ":"})
        return cleaned or "attachment.bin"

    async def _warm_cache_if_needed(self, *, hours_back: int) -> None:
        if self.email_store.count_recent(hours_back=hours_back) >= 20:
            return
        await self.scan_emails(
            params={
                "hours_back": hours_back,
                "triggered_by": "cache_warmup",
            }
        )

    async def _imap_subject_search(
        self,
        *,
        query: str,
        hours_back: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        terms = self._split_query_terms(query)
        if not terms:
            return []
        cutoff_dt = datetime.now(UTC) - timedelta(hours=max(1, hours_back))
        accounts = self._load_accounts()
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for account in accounts:
            host = str(account.get("host") or "")
            port = int(account.get("port") or 993)
            if not host:
                continue
            client = self.imap_client_factory(host, port)
            try:
                client.login(str(account.get("username") or ""), str(account.get("password") or ""))
                try:
                    client.select(str(account.get("folder") or "INBOX"), readonly=True)
                except TypeError:
                    client.select(str(account.get("folder") or "INBOX"))

                candidate_ids: list[bytes] = []
                for term in terms:
                    status, search_data = client.search(None, "SUBJECT", term)
                    if status != "OK":
                        continue
                    ids_blob = search_data[0] if search_data else b""
                    candidate_ids.extend(item for item in ids_blob.split() if item)

                for msg_num in candidate_ids:
                    fetch_status, fetched = client.fetch(msg_num, "(BODY.PEEK[])")
                    if fetch_status != "OK" or not fetched:
                        continue
                    raw_bytes = fetched[0][1] if isinstance(fetched[0], tuple) else None
                    if not isinstance(raw_bytes, (bytes, bytearray)):
                        continue
                    parsed = message_from_bytes(raw_bytes)
                    payload = self._extract_email_payload(
                        parsed,
                        account_name=str(account.get("name") or host),
                        msg_num=msg_num,
                    )
                    message_id = str(payload.get("message_id") or "").strip()
                    if not message_id or message_id in seen_ids:
                        continue
                    received_dt = self._parse_received_datetime(payload.get("received_at"))
                    if not self._is_within_cutoff(received_dt, cutoff_dt):
                        continue
                    record = self.email_store.upsert(
                        self._build_email_store_record(
                            payload,
                            category="",
                            account_name=str(account.get("name") or host),
                        )
                    )
                    results.append(self._record_to_overview(record, source="imap"))
                    seen_ids.add(message_id)
                    if len(results) >= limit:
                        return results
            except _EMAIL_IMAP_ERRORS as exc:
                # FALLBACK: search skips unhealthy mailboxes and continues with remaining accounts.
                logger.warning(
                    "email_scanner.search_account.fallback_used",
                    account_name=str(account.get("name") or host),
                    **_error_fields(exc),
                )
                continue
            finally:
                try:
                    client.logout()
                except _EMAIL_IMAP_ERRORS as exc:
                    # FALLBACK: logout failure should not discard already collected search results.
                    logger.warning(
                        "email_scanner.logout.fallback_used",
                        account_name=str(account.get("name") or host),
                        **_error_fields(exc),
                    )
        return results

    async def _fetch_email_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        key = str(message_id or "").strip()
        if not key:
            return None
        accounts = self._load_accounts()
        for account in accounts:
            host = str(account.get("host") or "")
            port = int(account.get("port") or 993)
            if not host:
                continue
            client = self.imap_client_factory(host, port)
            try:
                client.login(str(account.get("username") or ""), str(account.get("password") or ""))
                try:
                    client.select(str(account.get("folder") or "INBOX"), readonly=True)
                except TypeError:
                    client.select(str(account.get("folder") or "INBOX"))
                status, search_data = client.search(None, "HEADER", "Message-ID", key)
                if status != "OK":
                    continue
                ids_blob = search_data[0] if search_data else b""
                msg_ids = [item for item in ids_blob.split() if item]
                if not msg_ids:
                    continue
                fetch_status, fetched = client.fetch(msg_ids[0], "(BODY.PEEK[])")
                if fetch_status != "OK" or not fetched:
                    continue
                raw_bytes = fetched[0][1] if isinstance(fetched[0], tuple) else None
                if not isinstance(raw_bytes, (bytes, bytearray)):
                    continue
                parsed = message_from_bytes(raw_bytes)
                payload = self._extract_email_payload(
                    parsed,
                    account_name=str(account.get("name") or host),
                    msg_num=msg_ids[0],
                )
                payload["account_name"] = str(account.get("name") or host)
                return payload
            except _EMAIL_IMAP_ERRORS as exc:
                # FALLBACK: detail lookup skips unhealthy mailboxes and checks the next configured account.
                logger.warning(
                    "email_scanner.fetch_detail.fallback_used",
                    account_name=str(account.get("name") or host),
                    message_id=key,
                    **_error_fields(exc),
                )
                continue
            finally:
                try:
                    client.logout()
                except _EMAIL_IMAP_ERRORS as exc:
                    # FALLBACK: logout failure should not hide a successfully fetched email payload.
                    logger.warning(
                        "email_scanner.logout.fallback_used",
                        account_name=str(account.get("name") or host),
                        **_error_fields(exc),
                    )
        return None

    def _build_email_store_record(
        self,
        payload: dict[str, Any],
        *,
        category: str,
        attachment_paths: Any | None = None,
        account_name: str | None = None,
    ) -> dict[str, Any]:
        labels = [category] if str(category or "").strip() else []
        received_dt = self._parse_received_datetime(payload.get("received_at"))
        return {
            "message_id": str(payload.get("message_id") or ""),
            "subject": str(payload.get("subject") or ""),
            "from": str(payload.get("from") or ""),
            "to": str(payload.get("to") or ""),
            "date": to_local(received_dt).isoformat() if received_dt is not None else str(payload.get("received_at") or ""),
            "snippet": self._build_snippet(str(payload.get("body") or "")),
            "labels": labels,
            "cached_at": now_iso(),
            "has_body": self.email_store.get_body(str(payload.get("message_id") or "")) is not None,
            "account_name": account_name or payload.get("account_name"),
            "attachment_paths": attachment_paths or [],
            "category": category or payload.get("category"),
        }

    def _record_to_overview(self, record: dict[str, Any], *, source: str) -> dict[str, Any]:
        return {
            "message_id": str(record.get("message_id") or ""),
            "subject": str(record.get("subject") or ""),
            "from": str(record.get("from") or ""),
            "to": str(record.get("to") or ""),
            "date": str(record.get("date") or ""),
            "snippet": str(record.get("snippet") or ""),
            "labels": list(record.get("labels") or []),
            "source": source,
        }

    def _build_snippet(self, body: str, *, limit: int = 200) -> str:
        collapsed = " ".join(str(body or "").split())
        return collapsed[:limit]

    def _split_query_terms(self, query: str) -> list[str]:
        cleaned = str(query or "").strip().lower()
        if not cleaned:
            return []
        return [item for item in cleaned.split() if item]

    def _build_search_criteria(
        self,
        *,
        cutoff_dt: datetime | None,
        unread_only: bool,
    ) -> list[str]:
        since_dt = cutoff_dt or (datetime.now(UTC) - timedelta(hours=24))
        criteria = ["SINCE", since_dt.astimezone(UTC).strftime("%d-%b-%Y")]
        if unread_only:
            criteria.append("UNSEEN")
        return criteria

    def _parse_received_datetime(self, value: str | None) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = parsedate_to_datetime(raw)
        except _EMAIL_DATE_ERRORS as exc:
            # FALLBACK: malformed Date headers are treated as missing timestamps.
            logger.debug(
                "email_scanner.received_at_parse.fallback_used",
                raw_value=raw,
                **_error_fields(exc),
            )
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _is_within_cutoff(self, received_dt: datetime | None, cutoff_dt: datetime | None) -> bool:
        if cutoff_dt is None or received_dt is None:
            return True
        return received_dt >= cutoff_dt

    def _parse_hours_back(self, value: Any) -> int:
        if value is None or value == "":
            return 24
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 24
        return max(1, parsed)

    def _parse_triggered_by(self, value: Any) -> str:
        raw = str(value or "user").strip().lower()
        if raw in {"heartbeat", "scheduler", "scheduled"}:
            return "heartbeat"
        return "user"

    def _parse_limit(self, value: Any, *, default: int, maximum: int) -> int:
        if value is None or value == "":
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, min(maximum, parsed))

    def _resolve_scan_cutoff(
        self,
        *,
        triggered_by: str,
        hours_back: int,
        now: datetime,
        heartbeat_since_dt: datetime | None = None,
    ) -> datetime:
        if triggered_by == "heartbeat" and heartbeat_since_dt is not None:
            return heartbeat_since_dt
        return now - timedelta(hours=max(1, hours_back))

    async def _load_last_heartbeat_scan_started_at(
        self,
        *,
        triggered_by: str,
    ) -> datetime | None:
        if triggered_by != "heartbeat":
            return None
        if self._last_heartbeat_scan_started_at is not None:
            return self._last_heartbeat_scan_started_at

        getter = getattr(self.structured_store, "get_preference", None)
        if not callable(getter):
            return None

        try:
            raw_value = await getter(self.HEARTBEAT_SCAN_STARTED_AT_PREF_KEY)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            # FALLBACK: a missing heartbeat cursor only expands the next scan window.
            logger.warning(
                "email_scanner.heartbeat_cursor_load.fallback_used",
                **_error_fields(exc),
            )
            return None

        parsed = self._parse_iso_datetime(raw_value)
        if parsed is not None:
            self._last_heartbeat_scan_started_at = parsed
        return parsed

    async def _persist_last_heartbeat_scan_started_at(self, started_at: datetime) -> None:
        normalized = started_at.astimezone(UTC)
        self._last_heartbeat_scan_started_at = normalized

        setter = getattr(self.structured_store, "set_preference", None)
        if not callable(setter):
            return

        try:
            await setter(
                self.HEARTBEAT_SCAN_STARTED_AT_PREF_KEY,
                normalized.isoformat(),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            # FALLBACK: cursor persistence failure should not block the completed scan.
            logger.warning(
                "email_scanner.heartbeat_cursor_store.fallback_used",
                **_error_fields(exc),
            )
            return

    def _parse_iso_datetime(self, value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _remember_reported_message_ids(self, message_ids: Any) -> None:
        for raw_id in message_ids:
            message_id = str(raw_id or "").strip()
            if not message_id or message_id in self._recent_reported_message_id_set:
                continue
            self._recent_reported_message_ids.append(message_id)
            self._recent_reported_message_id_set.add(message_id)
            while len(self._recent_reported_message_ids) > 200:
                evicted = self._recent_reported_message_ids.popleft()
                self._recent_reported_message_id_set.discard(evicted)

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

    def _mark_message_as_read(self, client: Any, msg_num: bytes) -> None:
        if not self.mark_as_read:
            return
        store = getattr(client, "store", None)
        if not callable(store):
            return
        try:
            store(msg_num, "+FLAGS", "(\\Seen)")
        except _EMAIL_IMAP_ERRORS as exc:
            # FALLBACK: marking as read is best-effort and should not fail the scan result.
            logger.warning(
                "email_scanner.mark_as_read.fallback_used",
                **_error_fields(exc),
            )
            return

    def get_status(self, *, scheduler: Any | None = None) -> dict[str, Any]:
        accounts = self._load_accounts()
        next_scan_at = None
        if scheduler is not None and hasattr(scheduler, "get_job_next_run_iso"):
            next_scan_at = scheduler.get_job_next_run_iso("email_scan")
        return {
            "status": "scanning" if self._scan_in_progress else "enabled",
            "accounts": [
                str(item.get("username") or "")
                for item in accounts
                if str(item.get("username") or "").strip()
            ],
            "last_scan_at": localize_iso(self.last_scan_at),
            "next_scan_at": localize_iso(next_scan_at),
            "emails_processed": self.emails_processed,
        }
