from __future__ import annotations

from hypo_agent.channels.onebot11 import parse_onebot_private_message


def test_parse_onebot_private_message_from_plain_text() -> None:
    payload = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 10001,
        "message": "你好",
    }

    parsed = parse_onebot_private_message(payload)

    assert parsed is not None
    assert parsed.user_id == "10001"
    assert parsed.text == "你好"
    assert parsed.message_type == "private"


def test_parse_onebot_private_message_from_segments() -> None:
    payload = {
        "post_type": "message",
        "message_type": "private",
        "user_id": "10001",
        "message": [
            {"type": "text", "data": {"text": "看这个"}},
            {"type": "at", "data": {"qq": "123456"}},
            {"type": "image", "data": {"file": "a.jpg"}},
        ],
    }

    parsed = parse_onebot_private_message(payload)

    assert parsed is not None
    assert parsed.user_id == "10001"
    assert "看这个" in parsed.text
    assert "@123456" in parsed.text
    assert "[图片]" in parsed.text


def test_parse_onebot_private_message_uses_raw_message_fallback() -> None:
    payload = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 10001,
        "raw_message": "raw text",
    }

    parsed = parse_onebot_private_message(payload)

    assert parsed is not None
    assert parsed.text == "raw text"


def test_parse_onebot_private_message_ignores_group_message() -> None:
    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 9001,
        "user_id": 10001,
        "message": "hello",
    }

    parsed = parse_onebot_private_message(payload)

    assert parsed is None


def test_parse_onebot_private_message_ignores_non_message_event() -> None:
    payload = {
        "post_type": "notice",
        "message_type": "private",
        "user_id": 10001,
    }

    parsed = parse_onebot_private_message(payload)

    assert parsed is None
