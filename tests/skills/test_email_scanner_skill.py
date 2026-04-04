from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email import message_from_bytes
from email.header import decode_header, make_header

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.memory.email_store import EmailStore
import hypo_agent.skills.email_scanner_skill as email_scanner_skill_module
from hypo_agent.skills.email_scanner_skill import EmailScannerSkill


class StubStore:
    def __init__(self) -> None:
        self._processed: set[tuple[str, str]] = set()
        self.records: list[dict] = []
        self.preferences: dict[str, str] = {}

    async def has_processed_email(self, account_name: str, message_id: str) -> bool:
        return (account_name, message_id) in self._processed

    async def insert_processed_email(self, **kwargs) -> bool:
        key = (str(kwargs.get("account_name") or ""), str(kwargs.get("message_id") or ""))
        if key in self._processed:
            return False
        self._processed.add(key)
        self.records.append(dict(kwargs))
        return True

    async def get_preference(self, key: str) -> str | None:
        return self.preferences.get(key)

    async def set_preference(self, key: str, value: str) -> None:
        self.preferences[key] = value


def _write_rules(path: Path) -> None:
    path.write_text(
        """
rules:
  - name: important-boss
    from: "boss@example.com"
    subject_contains: "紧急"
    category: important
    skip_llm: true
  - name: archive-default
    from: "boss@example.com"
    category: archive
    skip_llm: false
""".strip(),
        encoding="utf-8",
    )


def _write_personal_rules(path: Path) -> None:
    path.write_text(
        """
user_preferences: |
  用户邮件分类偏好（用于判断邮件重要性）：

  【关注】：
  - 社团活动：只关注乒乓球社相关的，其他社团活动忽略
  - 人文院活动：只关注中国古诗词相关的内容，其他人文院活动不重要
  - 学生事务通知：只关注研究生必须要做的事情（如注册、选课、答辩等强制性通知），普通通知忽略
  - 公共服务处：只关注涉及 5 号楼、信息学院三号楼、体育馆的通知，其他不重要
  - 外部通知：只关注续费类通知，广告和营销内容忽略

  【忽略】：
  - 书院相关（全部忽略）
  - 产业报告（全部忽略）
  - 招聘信息（全部忽略）
  - 助教相关工作（全部忽略）
  - 广告和营销邮件（全部忽略）
rules:
  - name: archive-shuyuan-from
    from: "书院"
    category: archive
    skip_llm: true
  - name: archive-shuyuan-subject
    subject_contains: "书院"
    category: archive
    skip_llm: true
  - name: archive-industry-report
    subject_contains: "产业报告"
    category: archive
    skip_llm: true
  - name: archive-job-zhaopin
    subject_contains: "招聘"
    category: archive
    skip_llm: true
  - name: archive-job-zhaopinhui
    subject_contains: "招聘会"
    category: archive
    skip_llm: true
  - name: archive-job-qiuzhi
    subject_contains: "求职"
    category: archive
    skip_llm: true
  - name: archive-ta-zhujiao
    subject_contains: "助教"
    category: archive
    skip_llm: true
  - name: archive-ta-short
    subject_contains: "TA"
    category: archive
    skip_llm: true
  - name: important-pingpong-from
    from: "乒乓球"
    category: important
    skip_llm: true
  - name: important-pingpong-subject
    subject_contains: "乒乓球"
    category: important
    skip_llm: true
  - name: important-renewal
    subject_contains: "续费"
    category: important
    skip_llm: true
""".strip(),
        encoding="utf-8",
    )


def test_email_rule_first_match_wins(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    _write_rules(rules_path)
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
    )

    outcome = skill._apply_layer1_rules(
        {
            "from": "boss@example.com",
            "subject": "【紧急】今晚发布",
        }
    )

    assert outcome["matched"] is True
    assert outcome["rule_name"] == "important-boss"
    assert outcome["category"] == "important"


def test_rule_skip_llm_true_skips_layer2_and_layer3(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    _write_rules(rules_path)
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
    )

    result = asyncio.run(
        skill._classify_email(
            {
                "account_name": "main",
                "message_id": "<msg-1>",
                "from": "boss@example.com",
                "subject": "【紧急】今晚发布",
                "body": "请今晚处理",
            }
        )
    )
    assert result["category"] == "important"
    assert result["skip_llm"] is True
    assert result["layer"] == "rule"


def _write_secrets(path: Path) -> None:
    path.write_text(
        """
providers: {}
services:
  email:
    accounts:
      - name: main
        host: imap.main.local
        port: 993
        username: main@example.com
        password: pass-main
      - name: backup
        host: imap.backup.local
        port: 993
        username: backup@example.com
        password: pass-backup
""".strip(),
        encoding="utf-8",
    )


class FakeImap:
    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages
        self.store_calls: list[tuple[bytes, str, str]] = []
        self.fetch_calls: list[tuple[bytes, str]] = []
        self.search_calls: list[tuple[str, ...]] = []
        self.select_calls: list[tuple[str, bool | None]] = []
        self.logged_in = False

    def login(self, username: str, password: str):  # pragma: no cover - trivial
        del username, password
        self.logged_in = True
        return "OK", [b"logged in"]

    def select(self, folder: str, readonly: bool = False):
        self.select_calls.append((folder, readonly))
        return "OK", [b"selected"]

    def search(self, charset, *criteria):
        del charset
        normalized = tuple(str(item) for item in criteria)
        self.search_calls.append(normalized)
        matched_ids: list[bytes] = []
        if len(normalized) >= 2 and normalized[0] == "SUBJECT":
            term = normalized[1].strip('"')
            for index, raw in enumerate(self.messages):
                parsed = message_from_bytes(raw)
                subject = str(make_header(decode_header(str(parsed.get("Subject") or ""))))
                if term in subject:
                    matched_ids.append(str(index + 1).encode("utf-8"))
        elif len(normalized) >= 4 and normalized[0] == "HEADER" and normalized[1] == "Message-ID":
            target = normalized[2].strip('"') if len(normalized) == 3 else normalized[3].strip('"')
            for index, raw in enumerate(self.messages):
                parsed = message_from_bytes(raw)
                if target == str(parsed.get("Message-ID") or ""):
                    matched_ids.append(str(index + 1).encode("utf-8"))
        else:
            matched_ids = [str(i + 1).encode("utf-8") for i in range(len(self.messages))]
        ids = b" ".join(matched_ids)
        return "OK", [ids]

    def fetch(self, msg_id: bytes, query: str):
        self.fetch_calls.append((msg_id, query))
        index = int(msg_id.decode("utf-8")) - 1
        return "OK", [(b"RFC822", self.messages[index])]

    def store(self, msg_id: bytes, op: str, flags: str):
        self.store_calls.append((msg_id, op, flags))
        return "OK", [b"stored"]

    def logout(self):  # pragma: no cover - trivial
        return "OK", [b"bye"]


def _email_bytes(
    message_id: str,
    sender: str,
    subject: str,
    body: str,
    *,
    received_at: datetime | None = None,
) -> bytes:
    date_header = ""
    if received_at is not None:
        date_header = f"Date: {format_datetime(received_at.astimezone(UTC))}\n"
    content = (
        f"From: {sender}\n"
        f"Message-ID: {message_id}\n"
        f"Subject: {subject}\n"
        f"{date_header}"
        "\n"
        f"{body}\n"
    )
    return content.encode("utf-8")

def _email_with_attachment(
    message_id: str,
    sender: str,
    subject: str,
    body: str,
    *,
    received_at: datetime | None = None,
) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["Message-ID"] = message_id
    msg["Subject"] = subject
    if received_at is not None:
        msg["Date"] = format_datetime(received_at.astimezone(UTC))
    msg.attach(MIMEText(body, "plain", "utf-8"))

    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(b"invoice-data")
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="invoice.txt")
    msg.attach(attachment)
    return msg.as_bytes()


def test_scan_emails_iterates_accounts_and_isolates_failures(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()

    main_imap = FakeImap([_email_bytes("<m1>", "boss@example.com", "【紧急】发布", "请处理")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        raise RuntimeError("backup mailbox temporarily unavailable")

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    result = asyncio.run(skill.scan_emails(params={}))
    assert result["accounts_scanned"] == 1
    assert result["accounts_failed"] == 1
    assert result["new_emails"] == 1


def test_scan_emails_uses_peek_and_marks_seen_after_success_by_default(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    main_imap = FakeImap([_email_bytes("<m2>", "boss@example.com", "【紧急】发布", "请处理")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    asyncio.run(skill.scan_emails(params={}))
    assert main_imap.fetch_calls
    assert main_imap.fetch_calls[0][1] == "(BODY.PEEK[])"
    assert main_imap.store_calls == [(b"1", "+FLAGS", "(\\Seen)")]


def test_scan_emails_can_disable_mark_as_read(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    main_imap = FakeImap([_email_bytes("<m2b>", "boss@example.com", "【紧急】发布", "请处理")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
        mark_as_read=False,
    )

    asyncio.run(skill.scan_emails(params={}))

    assert main_imap.fetch_calls
    assert main_imap.store_calls == []


def test_scan_emails_does_not_mark_seen_when_processing_fails(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    main_imap = FakeImap([_email_bytes("<m2c>", "boss@example.com", "【紧急】发布", "请处理")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    async def fail_classify(_: dict[str, str]) -> dict[str, str]:
        raise RuntimeError("classification failed")

    skill._classify_email = fail_classify  # type: ignore[method-assign]

    result = asyncio.run(skill.scan_emails(params={}))

    assert result["accounts_failed"] == 1
    assert result["new_emails"] == 0
    assert main_imap.store_calls == []

def test_scan_emails_repeated_user_scans_still_return_recent_mail(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    messages = [_email_bytes("<m3>", "boss@example.com", "【紧急】发布", "请处理")]

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap(messages)
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    first = asyncio.run(skill.scan_emails(params={}))
    second = asyncio.run(skill.scan_emails(params={}))
    assert first["new_emails"] == 1
    assert second["new_emails"] == 1
    assert second["items"][0]["message_id"] == "<m3>"


def test_scan_emails_hours_back_filters_recent_window(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    now = datetime.now(UTC)
    messages = [
        _email_bytes(
            "<recent-1>",
            "boss@example.com",
            "两天内邮件",
            "请处理",
            received_at=now - timedelta(hours=36),
        ),
        _email_bytes(
            "<old-1>",
            "boss@example.com",
            "四天前邮件",
            "过期内容",
            received_at=now - timedelta(hours=96),
        ),
    ]

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap(messages)
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    result = asyncio.run(skill.scan_emails(params={"hours_back": 72}))

    assert result["new_emails"] == 1
    assert [item["message_id"] for item in result["items"]] == ["<recent-1>"]


def test_scan_emails_default_search_uses_since_not_unseen_only(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    main_imap = FakeImap([_email_bytes("<since-1>", "boss@example.com", "范围扫描", "内容")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    asyncio.run(skill.scan_emails(params={}))

    assert main_imap.search_calls
    criteria = main_imap.search_calls[0]
    assert "SINCE" in criteria
    assert "UNSEEN" not in criteria


def test_scan_emails_unread_only_adds_unseen_to_search(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    main_imap = FakeImap([_email_bytes("<unread-1>", "boss@example.com", "未读过滤", "内容")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    asyncio.run(skill.scan_emails(params={"unread_only": True}))

    assert main_imap.search_calls
    criteria = main_imap.search_calls[0]
    assert "SINCE" in criteria
    assert "UNSEEN" in criteria


def test_scheduled_scan_enqueues_email_scan_event(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    queue = EventQueue()
    store = StubStore()
    now = datetime.now(UTC)

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap(
                [
                    _email_bytes(
                        "<sched-1>",
                        "boss@example.com",
                        "Heartbeat 首次推送",
                        "内容",
                        received_at=now - timedelta(minutes=5),
                    )
                ]
            )
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=queue,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    async def _run() -> None:
        await skill.scheduled_scan()
        event = await queue.get()
        queue.task_done()
        assert event["event_type"] == "email_scan_trigger"
        assert "summary" in event

    asyncio.run(_run())


def test_scheduled_scan_does_not_repeat_already_reported_emails(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    queue = EventQueue()
    now = datetime.now(UTC)
    message = _email_bytes(
        "<heartbeat-1>",
        "boss@example.com",
        "Heartbeat 去重",
        "内容",
        received_at=now - timedelta(minutes=10),
    )

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap([message])
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=queue,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    async def _run() -> None:
        first = await skill.scheduled_scan()
        event = await queue.get()
        queue.task_done()
        second = await skill.scheduled_scan()

        assert first["new_emails"] == 1
        assert event["event_type"] == "email_scan_trigger"
        assert second["new_emails"] == 0
        assert queue.empty() is True

    asyncio.run(_run())


def test_heartbeat_scan_uses_persisted_last_scan_timestamp(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    now = datetime.now(UTC)
    store.preferences["email_scanner.last_heartbeat_scan_started_at"] = (
        now - timedelta(minutes=30)
    ).isoformat()
    messages = [
        _email_bytes(
            "<old-heartbeat>",
            "boss@example.com",
            "旧邮件",
            "旧内容",
            received_at=now - timedelta(hours=2),
        ),
        _email_bytes(
            "<new-heartbeat>",
            "boss@example.com",
            "新邮件",
            "新内容",
            received_at=now - timedelta(minutes=10),
        ),
    ]

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap(messages)
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    result = asyncio.run(skill.scan_emails(params={"triggered_by": "heartbeat"}))

    assert result["new_emails"] == 1
    assert [item["message_id"] for item in result["items"]] == ["<new-heartbeat>"]


def test_heartbeat_scan_persists_started_at_timestamp(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap([])
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    asyncio.run(skill.scan_emails(params={"triggered_by": "heartbeat"}))

    assert "email_scanner.last_heartbeat_scan_started_at" in store.preferences


def test_scan_emails_updates_email_store_index(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    email_store_root = tmp_path / "memory" / "emails"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    message = _email_bytes(
        "<cache-1>",
        "boss@example.com",
        "缓存测试",
        "这是正文前两百字以内的摘要内容",
        received_at=datetime.now(UTC) - timedelta(hours=2),
    )

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return FakeImap([message])
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
        email_store=EmailStore(root=email_store_root),
    )

    asyncio.run(skill.scan_emails(params={}))
    recent = skill.email_store.list_recent(hours_back=24, limit=10)

    assert len(recent) == 1
    assert recent[0]["message_id"] == "<cache-1>"
    assert "摘要内容" in recent[0]["snippet"]
    assert recent[0]["has_body"] is False


def test_search_emails_warms_cache_when_recent_cache_is_small(tmp_path: Path) -> None:
    email_store = EmailStore(root=tmp_path / "memory" / "emails")
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=tmp_path / "email_rules.yaml",
        email_store=email_store,
    )
    warmup_params: list[dict] = []

    async def fake_scan_emails(*, params=None) -> dict:
        warmup_params.append(dict(params or {}))
        email_store.upsert(
            {
                "message_id": "<warm-1>",
                "subject": "导师邮件",
                "from": "advisor@example.com",
                "to": "hyx@example.com",
                "date": datetime.now(UTC).isoformat(),
                "snippet": "讨论毕业相关安排",
                "labels": ["important"],
                "cached_at": datetime.now(UTC).isoformat(),
                "has_body": False,
            }
        )
        return {"new_emails": 1, "items": []}

    skill.scan_emails = fake_scan_emails  # type: ignore[method-assign]

    result = asyncio.run(skill.search_emails(query="毕业", hours_back=168))

    assert warmup_params == [{"hours_back": 168, "triggered_by": "cache_warmup"}]
    assert result["items"][0]["message_id"] == "<warm-1>"
    assert result["items"][0]["source"] == "local_cache"


def test_get_email_detail_fetches_from_imap_and_caches_body(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    email_store = EmailStore(root=tmp_path / "memory" / "emails")
    email_store.upsert(
        {
            "message_id": "<detail-1>",
            "subject": "论文安排",
            "from": "advisor@example.com",
            "to": "hyx@example.com",
            "date": datetime.now(UTC).isoformat(),
            "snippet": "请查看详细要求",
            "labels": ["important"],
            "cached_at": datetime.now(UTC).isoformat(),
            "has_body": False,
        }
    )
    main_imap = FakeImap(
        [
            _email_bytes(
                "<detail-1>",
                "advisor@example.com",
                "论文安排",
                "这是邮件正文全文，包含答辩材料要求",
            )
        ]
    )

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
        email_store=email_store,
    )

    result = asyncio.run(skill.get_email_detail(message_id="<detail-1>"))

    assert "答辩材料要求" in str(result["detail"]["body"])
    assert skill.email_store.get_body("<detail-1>") == "这是邮件正文全文，包含答辩材料要求\n"


def test_search_emails_uses_imap_subject_fallback_when_local_results_are_insufficient(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    email_store = EmailStore(root=tmp_path / "memory" / "emails")
    now = datetime.now(UTC)
    for idx in range(20):
        email_store.upsert(
            {
                "message_id": f"<prefill-{idx}>",
                "subject": f"普通邮件 {idx}",
                "from": "other@example.com",
                "to": "hyx@example.com",
                "date": (now - timedelta(hours=idx)).isoformat(),
                "snippet": "与查询无关",
                "labels": [],
                "cached_at": (now - timedelta(hours=idx)).isoformat(),
                "has_body": False,
            }
        )
        main_imap = FakeImap(
            [
                _email_bytes(
                    "<imap-search-1>",
                    "dept@example.com",
                    "registration notice",
                    "please complete registration this week",
                )
            ]
        )

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
        email_store=email_store,
    )

    result = asyncio.run(skill.search_emails(query="registration", hours_back=168))

    assert result["items"][0]["message_id"] == "<imap-search-1>"
    assert result["items"][0]["source"] == "imap"


def test_list_emails_returns_recent_overview_from_cache(tmp_path: Path) -> None:
    email_store = EmailStore(root=tmp_path / "memory" / "emails")
    email_store.upsert(
        {
            "message_id": "<list-1>",
            "subject": "最近邮件",
            "from": "club@example.com",
            "to": "hyx@example.com",
            "date": datetime.now(UTC).isoformat(),
            "snippet": "这里是邮件摘要",
            "labels": ["important"],
            "cached_at": datetime.now(UTC).isoformat(),
            "has_body": False,
        }
    )
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=tmp_path / "email_rules.yaml",
        email_store=email_store,
    )

    result = asyncio.run(skill.list_emails(hours_back=72, limit=30))

    assert result["items"][0]["message_id"] == "<list-1>"
    assert "body" not in result["items"][0]


class LlmRouterStub:
    def __init__(self) -> None:
        self.lightweight_calls = 0
        self.chat_calls = 0
        self.last_lightweight_prompt = ""

    async def call_lightweight_json(self, prompt: str, *, session_id: str | None = None) -> dict:
        del session_id
        self.lightweight_calls += 1
        self.last_lightweight_prompt = prompt
        return {"category": "important", "confidence": 0.92, "reason": "contains bill keyword"}

    async def call(self, model_name: str, messages: list[dict], *, session_id: str | None = None, tools=None):
        del model_name, messages, session_id, tools
        self.chat_calls += 1
        return "这是重点邮件摘要"


class SlowHeartbeatLlmRouterStub:
    def __init__(self) -> None:
        self.lightweight_calls = 0
        self.chat_calls = 0

    def call_lightweight_json(self, prompt: str, *, session_id: str | None = None):
        del prompt, session_id
        self.lightweight_calls += 1

        async def _pending() -> dict:
            await asyncio.sleep(3600)
            return {"category": "important", "confidence": 0.9, "reason": "slow"}

        return _pending()

    def call(self, model_name: str, messages: list[dict], *, session_id: str | None = None, tools=None):
        del model_name, messages, session_id, tools
        self.chat_calls += 1

        async def _pending() -> str:
            await asyncio.sleep(3600)
            return "slow summary"

        return _pending()


def test_layer2_calls_lightweight_json_for_unmatched_mail(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    router = LlmRouterStub()
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=router,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
    )

    outcome = asyncio.run(
        skill._classify_email(
            {
                "account_name": "main",
                "message_id": "<layer2-1>",
                "from": "finance@example.com",
                "subject": "Monthly invoice",
                "body": "Please pay before Friday",
            }
        )
    )

    assert router.lightweight_calls == 1
    assert outcome["category"] == "important"


def test_layer3_calls_default_model_for_important_and_system(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    router = LlmRouterStub()
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=router,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
    )

    outcome = asyncio.run(
        skill._classify_email(
            {
                "account_name": "main",
                "message_id": "<layer3-1>",
                "from": "finance@example.com",
                "subject": "Monthly invoice",
                "body": "Please pay before Friday",
            }
        )
    )

    assert outcome["category"] in {"important", "system"}
    assert router.chat_calls == 1
    assert "摘要" in outcome["summary"]


def test_heartbeat_classification_times_out_layer2_and_skips_layer3(tmp_path: Path, monkeypatch) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    router = SlowHeartbeatLlmRouterStub()
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=router,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
    )

    async def fake_wait_for(awaitable, timeout):
        del timeout
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(email_scanner_skill_module.asyncio, "wait_for", fake_wait_for)

    outcome = asyncio.run(
        skill._classify_email(
            {
                "account_name": "main",
                "message_id": "<heartbeat-layer2-1>",
                "from": "finance@example.com",
                "subject": "Monthly invoice",
                "body": "Please pay before Friday",
            },
            triggered_by="heartbeat",
        )
    )

    assert router.lightweight_calls == 1
    assert router.chat_calls == 0
    assert outcome["category"] == "low_priority"
    assert outcome["summary"] == "Monthly invoice"


def test_email_attachments_saved_under_memory_email_attachments(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    attachments_root = tmp_path / "memory" / "email_attachments"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    main_imap = FakeImap(
        [_email_with_attachment("<att-1>", "boss@example.com", "【紧急】附件测试", "请查看附件")]
    )

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
        attachments_root=attachments_root,
    )

    result = asyncio.run(skill.scan_emails(params={}))
    assert result["new_emails"] >= 1
    assert store.records
    attachment_paths = store.records[0].get("attachment_paths") or []
    assert attachment_paths
    assert "memory/email_attachments" in attachment_paths[0].replace("\\", "/")
    first_attachment = Path(attachment_paths[0])
    assert first_attachment.exists()


def test_bootstrap_rules_returns_draft_without_writing_file(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
    )

    original = rules_path.read_text(encoding="utf-8")
    draft = asyncio.run(skill.bootstrap_rules())

    assert draft["draft_id"]
    assert "rules:" in draft["yaml_content"]
    assert rules_path.read_text(encoding="utf-8") == original


def test_bootstrap_rules_confirm_writes_email_rules_yaml(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
    )

    draft = asyncio.run(skill.bootstrap_rules())
    result = asyncio.run(
        skill.scan_emails(
            params={"bootstrap_confirm": True, "draft_id": draft["draft_id"]}
        )
    )

    assert result["bootstrap_confirmed"] is True
    saved = rules_path.read_text(encoding="utf-8")
    assert "rules:" in saved
    assert saved.strip() == draft["yaml_content"].strip()


def test_scan_emails_without_rules_still_scans_and_calls_layer2(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    rules_path.write_text("rules: []\n", encoding="utf-8")
    secrets_path = tmp_path / "secrets.yaml"
    _write_secrets(secrets_path)
    store = StubStore()
    router = LlmRouterStub()
    main_imap = FakeImap([_email_bytes("<m-no-rules>", "finance@example.com", "Monthly invoice", "Please pay")])

    def factory(host: str, port: int):
        del port
        if host == "imap.main.local":
            return main_imap
        return FakeImap([])

    skill = EmailScannerSkill(
        structured_store=store,
        model_router=router,
        message_queue=None,
        rules_path=rules_path,
        secrets_path=secrets_path,
        imap_client_factory=factory,
    )

    result = asyncio.run(skill.scan_emails(params={}))

    assert result["new_emails"] == 1
    assert "requires_confirmation" not in result
    assert router.lightweight_calls == 1


def test_personal_layer1_rules_match_archive_and_important_categories(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    _write_personal_rules(rules_path)
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=rules_path,
    )

    archive_hit = skill._apply_layer1_rules(
        {"from": "career@example.com", "subject": "周末招聘会安排"}
    )
    important_hit = skill._apply_layer1_rules(
        {"from": "乒乓球社 <club@example.com>", "subject": "本周训练通知"}
    )

    assert archive_hit["matched"] is True
    assert archive_hit["category"] == "archive"
    assert archive_hit["skip_llm"] is True
    assert important_hit["matched"] is True
    assert important_hit["category"] == "important"
    assert important_hit["skip_llm"] is True


def test_layer2_prompt_includes_user_preferences_from_rules_config(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    _write_personal_rules(rules_path)
    router = LlmRouterStub()
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=router,
        message_queue=None,
        rules_path=rules_path,
    )

    asyncio.run(
        skill._classify_email(
            {
                "account_name": "main",
                "message_id": "<pref-1>",
                "from": "service@example.com",
                "subject": "人文院讲座报名",
                "body": "本周举办讲座",
            }
        )
    )

    assert "用户邮件分类偏好" in router.last_lightweight_prompt
    assert "只关注乒乓球社相关的" in router.last_lightweight_prompt
    assert "广告和营销邮件" in router.last_lightweight_prompt


def test_email_scanner_tool_descriptions_are_task_oriented(tmp_path: Path) -> None:
    skill = EmailScannerSkill(
        structured_store=StubStore(),
        model_router=None,
        message_queue=None,
        rules_path=tmp_path / "email_rules.yaml",
    )

    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in skill.tools
    }
    scan_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "scan_emails")
    scan_properties = scan_tool["function"]["parameters"]["properties"]
    list_tool = next(tool for tool in skill.tools if tool["function"]["name"] == "list_emails")
    list_properties = list_tool["function"]["parameters"]["properties"]

    assert "看邮件" in descriptions["scan_emails"]
    assert "最近三天的邮件" in descriptions["scan_emails"]
    assert "后续搜索会更快更准" in descriptions["scan_emails"]
    assert "hours_back" in scan_properties
    assert "unread_only" in scan_properties
    assert "不要在第一次搜索失败后就告诉用户搜不到" in descriptions["search_emails"]
    assert "list_emails" in descriptions["search_emails"]
    assert "hours_back" in next(
        tool for tool in skill.tools if tool["function"]["name"] == "search_emails"
    )["function"]["parameters"]["properties"]
    assert "列出最近的邮件概览" in descriptions["list_emails"]
    assert "from_filter" in list_properties
    assert "message_id" in descriptions["get_email_detail"]
    assert "缓存到本地" in descriptions["get_email_detail"]
