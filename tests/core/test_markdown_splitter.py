from __future__ import annotations

from hypo_agent.core.markdown_splitter import BlockType, split_markdown, split_markdown_blocks


def test_split_markdown_empty() -> None:
    assert split_markdown("") == []


def test_split_markdown_plain_text() -> None:
    blocks = split_markdown("# 标题\n\n正文\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.TEXT
    assert blocks[0].content == "# 标题\n\n正文\n"


def test_split_markdown_code_block() -> None:
    blocks = split_markdown("前文\n```python\nprint('x')\n```\n后文\n")

    assert [block.type for block in blocks] == [
        BlockType.TEXT,
        BlockType.CODE_BLOCK,
        BlockType.TEXT,
    ]
    assert blocks[1].language == "python"
    assert "print('x')" in blocks[1].content


def test_split_markdown_mermaid_block() -> None:
    blocks = split_markdown("```mermaid\ngraph TD\nA-->B\n```\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.MERMAID
    assert blocks[0].language == "mermaid"


def test_split_markdown_math_block() -> None:
    blocks = split_markdown("说明\n$$\nE=mc^2\n$$\n结尾\n")

    assert [block.type for block in blocks] == [
        BlockType.TEXT,
        BlockType.MATH_BLOCK,
        BlockType.TEXT,
    ]
    assert blocks[1].content == "$$\nE=mc^2\n$$\n"


def test_split_markdown_table() -> None:
    blocks = split_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.TABLE


def test_split_markdown_horizontal_rule_and_image() -> None:
    blocks = split_markdown("前文\n\n---\n\n![cat](https://example.com/cat.png)\n\n后文\n")

    assert [block.type for block in blocks] == [
        BlockType.TEXT,
        BlockType.HORIZONTAL_RULE,
        BlockType.TEXT,
        BlockType.IMAGE,
        BlockType.TEXT,
    ]


def test_split_markdown_mixed_content() -> None:
    content = (
        "# 标题\n\n"
        "```python\nprint(1)\n```\n\n"
        "$$x^2$$\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "```mermaid\ngraph TD\nA-->B\n```\n"
        "尾声\n"
    )

    blocks = split_markdown(content)

    assert [block.type for block in blocks] == [
        BlockType.TEXT,
        BlockType.CODE_BLOCK,
        BlockType.TEXT,
        BlockType.MATH_BLOCK,
        BlockType.TABLE,
        BlockType.MERMAID,
        BlockType.TEXT,
    ]


def test_split_markdown_only_code_block() -> None:
    blocks = split_markdown("```text\nhello\n```\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.CODE_BLOCK
    assert blocks[0].language == "text"


def test_split_markdown_does_not_detect_table_inside_code_block() -> None:
    blocks = split_markdown("```text\n| not | table |\n| --- | --- |\n```\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.CODE_BLOCK


def test_split_markdown_keeps_inline_math_inside_text_block() -> None:
    blocks = split_markdown("正文里有 $a+b$ 行内公式。\n")

    assert len(blocks) == 1
    assert blocks[0].type is BlockType.TEXT
    assert "$a+b$" in blocks[0].content


def test_split_markdown_legacy_wrapper_still_preserves_content() -> None:
    content = (
        "前文\n"
        "```mermaid\ngraph TD\nA-->B\n```\n"
        "$$x^2$$\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "后文\n"
    )

    blocks = split_markdown_blocks(content)

    assert "".join(block["content"] for block in blocks) == content
