from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hypo_agent.models import Attachment


@dataclass(slots=True)
class RichResponse:
    text: str = ""
    compressed_meta: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[Attachment | dict[str, Any]] = field(default_factory=list)
