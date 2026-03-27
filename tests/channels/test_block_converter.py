from __future__ import annotations

from hypo_agent.channels.notion.block_converter import blocks_to_markdown, markdown_to_blocks


def test_blocks_to_markdown_renders_supported_block_types() -> None:
    blocks = [
        {
            "id": "h1",
            "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "plain_text": "Title", "annotations": {}}]},
        },
        {
            "id": "p1",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "bold",
                        "annotations": {"bold": True},
                    },
                    {
                        "type": "text",
                        "plain_text": " and link",
                        "annotations": {},
                        "href": "https://example.com",
                    },
                ]
            },
        },
        {
            "id": "todo1",
            "type": "to_do",
            "to_do": {
                "checked": True,
                "rich_text": [{"type": "text", "plain_text": "Ship it", "annotations": {}}],
            },
        },
        {
            "id": "code1",
            "type": "code",
            "code": {
                "language": "python",
                "rich_text": [{"type": "text", "plain_text": "print('hi')", "annotations": {}}],
            },
        },
        {
            "id": "quote1",
            "type": "quote",
            "quote": {
                "rich_text": [{"type": "text", "plain_text": "quoted", "annotations": {}}],
            },
        },
        {"id": "div1", "type": "divider", "divider": {}},
        {
            "id": "img1",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://example.com/image.png"},
                "caption": [{"type": "text", "plain_text": "diagram", "annotations": {}}],
            },
        },
        {
            "id": "callout1",
            "type": "callout",
            "callout": {
                "icon": {"emoji": "💡"},
                "rich_text": [{"type": "text", "plain_text": "Tip", "annotations": {}}],
            },
        },
        {
            "id": "bookmark1",
            "type": "bookmark",
            "bookmark": {"url": "https://example.com"},
        },
        {
            "id": "table1",
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "has_row_header": False,
                "children": [
                    {
                        "id": "row1",
                        "type": "table_row",
                        "table_row": {
                            "cells": [
                                [{"type": "text", "plain_text": "A", "annotations": {}}],
                                [{"type": "text", "plain_text": "B", "annotations": {}}],
                            ]
                        },
                    },
                    {
                        "id": "row2",
                        "type": "table_row",
                        "table_row": {
                            "cells": [
                                [{"type": "text", "plain_text": "1", "annotations": {}}],
                                [{"type": "text", "plain_text": "2", "annotations": {}}],
                            ]
                        },
                    },
                ],
            },
        },
        {
            "id": "toggle1",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "plain_text": "More", "annotations": {}}],
                "children": [
                    {
                        "id": "togglechild",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "plain_text": "inside", "annotations": {}}
                            ]
                        },
                    }
                ],
            },
        },
        {"id": "unsupported", "type": "embed", "embed": {"url": "https://example.com/embed"}},
    ]

    markdown = blocks_to_markdown(blocks)

    assert "# Title" in markdown
    assert "**bold**" in markdown
    assert "[ and link](https://example.com)" in markdown
    assert "- [x] Ship it" in markdown
    assert "```python" in markdown
    assert "> quoted" in markdown
    assert "---" in markdown
    assert "![diagram](https://example.com/image.png)" in markdown
    assert "> 💡 Tip" in markdown
    assert "[https://example.com](https://example.com)" in markdown
    assert "| A | B |" in markdown
    assert "<details><summary>More</summary>" in markdown
    assert "[不支持的块类型: embed]" in markdown


def test_markdown_to_blocks_parses_common_markdown() -> None:
    markdown = """# Title

Plain text

- first
- second

1. one
1. two

- [ ] todo

> note

---

```python
print("hi")
```

![diagram](https://example.com/image.png)
"""

    blocks = markdown_to_blocks(markdown)
    types = [block["type"] for block in blocks]

    assert types == [
        "heading_1",
        "paragraph",
        "bulleted_list_item",
        "bulleted_list_item",
        "numbered_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
        "divider",
        "code",
        "image",
    ]
    assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "Title"
    assert blocks[6]["to_do"]["checked"] is False
    assert blocks[9]["code"]["language"] == "python"


def test_nested_blocks_are_grouped_as_children() -> None:
    markdown = "- parent\n  - child\n    - grandchild\n"

    blocks = markdown_to_blocks(markdown)

    assert len(blocks) == 1
    assert blocks[0]["type"] == "bulleted_list_item"
    child = blocks[0]["bulleted_list_item"]["children"][0]
    assert child["type"] == "bulleted_list_item"
    grandchild = child["bulleted_list_item"]["children"][0]
    assert grandchild["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "grandchild"


def test_rich_text_formatting_is_preserved_in_markdown() -> None:
    blocks = [
        {
            "id": "p1",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "plain_text": "bold", "annotations": {"bold": True}},
                    {"type": "text", "plain_text": " italic", "annotations": {"italic": True}},
                    {
                        "type": "text",
                        "plain_text": " code",
                        "annotations": {"code": True},
                    },
                    {
                        "type": "text",
                        "plain_text": " link",
                        "annotations": {},
                        "href": "https://example.com",
                    },
                ]
            },
        }
    ]

    markdown = blocks_to_markdown(blocks)

    assert "**bold**" in markdown
    assert "* italic*" in markdown
    assert "` code`" in markdown
    assert "[ link](https://example.com)" in markdown


def test_roundtrip_keeps_basic_structure() -> None:
    markdown = """## Section

- alpha
- [x] shipped

> note
"""

    rendered = blocks_to_markdown(markdown_to_blocks(markdown))

    assert "## Section" in rendered
    assert "- alpha" in rendered
    assert "- [x] shipped" in rendered
    assert "> note" in rendered
