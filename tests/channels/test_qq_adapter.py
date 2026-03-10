from __future__ import annotations

from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.models import Message


def test_qq_adapter_downgrades_markdown_inline_styles() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")

    text = adapter.downgrade_markdown("**粗体** *斜体* `代码`")

    assert "**" not in text
    assert "*" not in text
    assert "`" not in text
    assert "粗体" in text
    assert "斜体" in text
    assert "代码" in text


def test_qq_adapter_keeps_code_block_indentation() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    content = "示例:\n```python\n  a = 1\n    print(a)\n```"

    text = adapter.downgrade_markdown(content)

    assert "a = 1" in text
    assert "    print(a)" in text
    assert "```" not in text


def test_qq_adapter_converts_markdown_table_to_plain_text() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    content = "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |"

    text = adapter.downgrade_markdown(content)

    assert "列A | 列B" in text
    assert "1 | 2" in text


def test_qq_adapter_replaces_message_tag_with_emoji_prefix() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    message = Message(
        text="提醒：开会",
        sender="assistant",
        session_id="main",
        message_tag="reminder",
    )

    rendered = adapter.render_message_text(message)

    assert rendered.startswith("🔔 ")
    assert "提醒：开会" in rendered


def test_qq_adapter_splits_long_message() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    source = "a" * 55

    chunks = adapter.split_message(source, limit=20)

    assert len(chunks) == 3
    assert all(len(item) <= 20 for item in chunks)
    assert "".join(chunks) == source


def test_qq_adapter_adds_access_token_to_request_url() -> None:
    adapter = QQAdapter(
        napcat_http_url="http://localhost:3008",
        napcat_http_token="token-123",
    )

    url = adapter._build_request_url("/send_private_msg")

    assert url == "http://localhost:3008/send_private_msg?access_token=token-123"
