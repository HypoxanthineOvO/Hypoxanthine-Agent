from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.skills.email_scanner_skill import EmailScannerSkill


class StubStore:
    async def has_processed_email(self, account_name: str, message_id: str) -> bool:
        del account_name, message_id
        return False

    async def insert_processed_email(self, **kwargs):
        del kwargs
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
