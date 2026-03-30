from __future__ import annotations

import asyncio
import json

from hypo_agent.core.feishu_adapter import FEISHU_CARD_CHAR_LIMIT, FeishuAdapter
from hypo_agent.core.rich_response import RichResponse


def test_feishu_adapter_formats_markdown_card() -> None:
    adapter = FeishuAdapter()

    payloads = asyncio.run(adapter.format(RichResponse(text="**hello**")))

    assert len(payloads) == 1
    assert payloads[0]["msg_type"] == "interactive"
    card = json.loads(payloads[0]["content"])
    assert card["schema"] == "2.0"
    assert card["body"]["elements"][0] == {"tag": "markdown", "content": "**hello**"}


def test_feishu_adapter_splits_long_markdown_into_multiple_cards() -> None:
    adapter = FeishuAdapter()
    text = "a" * (FEISHU_CARD_CHAR_LIMIT + 10)

    payloads = asyncio.run(adapter.format(RichResponse(text=text)))

    assert len(payloads) == 2
    first = json.loads(payloads[0]["content"])["body"]["elements"][0]["content"]
    second = json.loads(payloads[1]["content"])["body"]["elements"][0]["content"]
    assert len(first) == FEISHU_CARD_CHAR_LIMIT
    assert len(second) == 10


def test_feishu_adapter_converts_pipe_table_to_table_component() -> None:
    adapter = FeishuAdapter()
    text = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"

    payloads = asyncio.run(adapter.format(RichResponse(text=text)))
    card = json.loads(payloads[0]["content"])
    elements = card["body"]["elements"]

    assert elements[0]["tag"] == "table"
    assert elements[0]["columns"][0]["display_name"] == "a"
    assert elements[0]["columns"][1]["display_name"] == "b"
    assert elements[0]["rows"][0]["c0"] == "1"
    assert elements[0]["rows"][0]["c1"] == "2"
