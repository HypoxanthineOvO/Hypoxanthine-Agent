from __future__ import annotations

from hypo_agent.core.qq_text_renderer import render_qq_plaintext


def test_bold() -> None:
    assert render_qq_plaintext("**测试**") == "【测试】"


def test_italic() -> None:
    assert render_qq_plaintext("*测试*") == "测试"


def test_link() -> None:
    assert render_qq_plaintext("[链接](https://x.com)") == "链接 (https://x.com)"


def test_quote() -> None:
    assert render_qq_plaintext("> 引用内容") == "「引用内容」"


def test_divider() -> None:
    assert render_qq_plaintext("---") == "————————"


def test_strikethrough() -> None:
    assert render_qq_plaintext("~~删除~~") == "删除"


def test_list() -> None:
    assert render_qq_plaintext("- 项目") == "• 项目"


def test_heading_dynamic_mapping() -> None:
    content = "## 总结\n### 细节\n#### 备注\n"

    assert render_qq_plaintext(content) == "『总结』\n『总结』-细节\n备注"


def test_inline_code_preserved() -> None:
    assert render_qq_plaintext("这里是 `code`") == "这里是 code"


def test_inline_code_preserves_spaces() -> None:
    assert render_qq_plaintext("use `pip install` to install") == "use pip install to install"


def test_bold_inside_inline_code() -> None:
    assert render_qq_plaintext("`**not bold**`") == "**not bold**"


def test_nested_formatting() -> None:
    assert render_qq_plaintext("**加粗 *斜体***") == "【加粗 斜体】"
