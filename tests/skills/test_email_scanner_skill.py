from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.skills.email_scanner_skill import EmailScannerSkill


class StubStore:
    def __init__(self) -> None:
        self._processed: set[tuple[str, str]] = set()

    async def has_processed_email(self, account_name: str, message_id: str) -> bool:
        return (account_name, message_id) in self._processed

    async def insert_processed_email(self, **kwargs) -> bool:
        key = (str(kwargs.get("account_name") or ""), str(kwargs.get("message_id") or ""))
        if key in self._processed:
            return False
        self._processed.add(key)
        return True


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
        self.logged_in = False

    def login(self, username: str, password: str):  # pragma: no cover - trivial
        del username, password
        self.logged_in = True
        return "OK", [b"logged in"]

    def select(self, folder: str):
        del folder
        return "OK", [b"selected"]

    def search(self, charset, criterion):
        del charset, criterion
        ids = b" ".join(str(i + 1).encode("utf-8") for i in range(len(self.messages)))
        return "OK", [ids]

    def fetch(self, msg_id: bytes, query: str):
        del query
        index = int(msg_id.decode("utf-8")) - 1
        return "OK", [(b"RFC822", self.messages[index])]

    def store(self, msg_id: bytes, op: str, flags: str):
        self.store_calls.append((msg_id, op, flags))
        return "OK", [b"stored"]

    def logout(self):  # pragma: no cover - trivial
        return "OK", [b"bye"]


def _email_bytes(message_id: str, sender: str, subject: str, body: str) -> bytes:
    content = (
        f"From: {sender}\n"
        f"Message-ID: {message_id}\n"
        f"Subject: {subject}\n"
        "\n"
        f"{body}\n"
    )
    return content.encode("utf-8")


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


def test_scan_emails_marks_seen_after_processing(tmp_path: Path) -> None:
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
    assert main_imap.store_calls
    assert main_imap.store_calls[0][1] == "+FLAGS"
    assert main_imap.store_calls[0][2] == "(\\Seen)"


def test_scan_emails_deduplicates_with_processed_emails(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    store = StubStore()
    messages = [_email_bytes("<m3>", "boss@example.com", "【紧急】发布", "请处理")]

    def factory(host: str, port: int):
        del host, port
        return FakeImap(messages)

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
    assert first["new_emails"] >= 1
    assert second["new_emails"] == 0


def test_scheduled_scan_enqueues_email_scan_event(tmp_path: Path) -> None:
    rules_path = tmp_path / "email_rules.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    _write_rules(rules_path)
    _write_secrets(secrets_path)
    queue = EventQueue()
    store = StubStore()

    def factory(host: str, port: int):
        del host, port
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


class LlmRouterStub:
    def __init__(self) -> None:
        self.lightweight_calls = 0
        self.chat_calls = 0

    async def call_lightweight_json(self, prompt: str, *, session_id: str | None = None) -> dict:
        del prompt, session_id
        self.lightweight_calls += 1
        return {"category": "important", "confidence": 0.92, "reason": "contains bill keyword"}

    async def call(self, model_name: str, messages: list[dict], *, session_id: str | None = None, tools=None):
        del model_name, messages, session_id, tools
        self.chat_calls += 1
        return "这是重点邮件摘要"


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
