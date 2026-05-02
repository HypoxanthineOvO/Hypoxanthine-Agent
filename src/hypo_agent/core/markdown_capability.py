from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RenderStrategy(Enum):
    PASSTHROUGH = "passthrough"
    IMAGE = "image"
    DOWNGRADE_TEXT = "downgrade_text"
    SPLIT_MESSAGE = "split_message"
    CARD_COMPONENT = "card_component"


@dataclass(slots=True)
class ChannelMarkdownCapability:
    channel: str
    heading_max_level: int
    bold: bool = True
    italic: bool = True
    strikethrough: bool = True
    link: bool = True
    ordered_list: bool = True
    unordered_list: bool = True
    blockquote: bool = True
    horizontal_rule: bool = True
    code_block: bool = False
    inline_code: bool = False
    table: RenderStrategy = RenderStrategy.IMAGE
    math_formula: RenderStrategy = RenderStrategy.IMAGE
    mermaid: RenderStrategy = RenderStrategy.IMAGE
    image_embed: bool = True
    image_split_required: bool = False
    msg_type_markdown: bool = False


QQ_CAPABILITY = ChannelMarkdownCapability(
    channel="qq",
    heading_max_level=2,
    code_block=True,
    inline_code=True,
    table=RenderStrategy.PASSTHROUGH,
    math_formula=RenderStrategy.IMAGE,
    mermaid=RenderStrategy.IMAGE,
    image_embed=True,
    msg_type_markdown=True,
)

WEIXIN_CAPABILITY = ChannelMarkdownCapability(
    channel="weixin",
    heading_max_level=6,
    code_block=True,
    inline_code=True,
    table=RenderStrategy.PASSTHROUGH,
    math_formula=RenderStrategy.IMAGE,
    mermaid=RenderStrategy.IMAGE,
    image_embed=True,
    image_split_required=False,
)

FEISHU_CAPABILITY = ChannelMarkdownCapability(
    channel="feishu",
    heading_max_level=4,
    code_block=True,
    inline_code=True,
    table=RenderStrategy.CARD_COMPONENT,
    math_formula=RenderStrategy.IMAGE,
    mermaid=RenderStrategy.IMAGE,
    image_embed=True,
)

WEBUI_CAPABILITY = ChannelMarkdownCapability(
    channel="webui",
    heading_max_level=6,
    code_block=True,
    inline_code=True,
    table=RenderStrategy.PASSTHROUGH,
    math_formula=RenderStrategy.PASSTHROUGH,
    mermaid=RenderStrategy.PASSTHROUGH,
    image_embed=True,
)

CAPABILITY_MAP = {
    "qq": QQ_CAPABILITY,
    "weixin": WEIXIN_CAPABILITY,
    "feishu": FEISHU_CAPABILITY,
    "webui": WEBUI_CAPABILITY,
}
