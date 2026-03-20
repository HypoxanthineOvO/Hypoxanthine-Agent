from __future__ import annotations

from hypo_agent.core.markdown_splitter import split_markdown_blocks


def test_split_code_block() -> None:
    blocks = split_markdown_blocks("前文\n```python\nprint('x')\n```\n后文\n")

    assert [block["type"] for block in blocks] == ["text", "code", "text"]
    assert "print('x')" in blocks[1]["content"]


def test_split_mermaid() -> None:
    blocks = split_markdown_blocks("```mermaid\ngraph TD\nA-->B\n```\n")

    assert len(blocks) == 1
    assert blocks[0]["type"] == "mermaid"


def test_split_math_block() -> None:
    blocks = split_markdown_blocks("说明\n$$E=mc^2$$\n结尾\n")

    assert [block["type"] for block in blocks] == ["text", "math", "text"]
    assert blocks[1]["content"] == "$$E=mc^2$$\n"


def test_split_table() -> None:
    blocks = split_markdown_blocks("| A | B |\n| --- | --- |\n| 1 | 2 |\n")

    assert len(blocks) == 1
    assert blocks[0]["type"] == "table"


def test_split_mixed() -> None:
    content = (
        "# 标题\n\n"
        "```python\nprint(1)\n```\n\n"
        "$$a+b$$\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "尾声\n"
    )

    blocks = split_markdown_blocks(content)

    assert [block["type"] for block in blocks] == ["text", "code", "text", "math", "table", "text"]


def test_code_block_contains_table_syntax() -> None:
    blocks = split_markdown_blocks("```text\n| not | table |\n| --- | --- |\n```\n")

    assert len(blocks) == 1
    assert blocks[0]["type"] == "code"


def test_no_content_lost() -> None:
    content = (
        "前文\n"
        "```mermaid\ngraph TD\nA-->B\n```\n"
        "$$x^2$$\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "后文\n"
    )

    blocks = split_markdown_blocks(content)

    assert "".join(block["content"] for block in blocks) == content
