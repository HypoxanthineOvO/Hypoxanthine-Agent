from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from hypo_agent.models import Message

_CODE_BLOCK_PATTERN = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_TABLE_ALIGN_ROW_PATTERN = re.compile(r"^\|\s*[:\-\s|]+\|$")


class QQAdapter:
    def __init__(
        self,
        *,
        napcat_http_url: str,
        napcat_http_token: str | None = None,
        send_delay_seconds: float = 0.2,
        request_timeout_seconds: float = 10.0,
        message_limit: int = 4500,
    ) -> None:
        self.napcat_http_url = napcat_http_url.rstrip("/")
        self.napcat_http_token = (napcat_http_token or "").strip() or None
        self.send_delay_seconds = max(0.0, send_delay_seconds)
        self.request_timeout_seconds = max(1.0, request_timeout_seconds)
        self.message_limit = max(100, int(message_limit))

    def render_message_text(self, message: Message) -> str:
        text = self.downgrade_markdown((message.text or "").strip())
        if not text:
            return ""
        emoji = self._tag_emoji(message.message_tag)
        if emoji and not text.startswith(f"{emoji} "):
            return f"{emoji} {text}"
        return text

    def downgrade_markdown(self, text: str) -> str:
        if not text:
            return ""

        code_blocks: list[str] = []

        def _stash_code(match: re.Match[str]) -> str:
            token = f"__QQ_CODE_BLOCK_{len(code_blocks)}__"
            code_blocks.append(match.group(1).strip("\n"))
            return token

        staged = _CODE_BLOCK_PATTERN.sub(_stash_code, text)
        staged = self._convert_tables(staged)
        staged = staged.replace("**", "").replace("*", "").replace("`", "")

        for idx, code in enumerate(code_blocks):
            staged = staged.replace(f"__QQ_CODE_BLOCK_{idx}__", code)

        lines = [line.rstrip() for line in staged.splitlines()]
        return "\n".join(lines).strip()

    def split_message(self, text: str, *, limit: int | None = None) -> list[str]:
        effective_limit = self.message_limit if limit is None else max(1, int(limit))
        if len(text) <= effective_limit:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > effective_limit:
                if current:
                    chunks.append(current)
                    current = ""
                for idx in range(0, len(line), effective_limit):
                    chunks.append(line[idx : idx + effective_limit])
                continue

            if len(current) + len(line) <= effective_limit:
                current += line
                continue
            if current:
                chunks.append(current)
            current = line

        if current:
            chunks.append(current)
        return chunks

    async def send_message(self, *, user_id: str, message: Message) -> bool:
        rendered = self.render_message_text(message)
        if not rendered:
            return True
        chunks = self.split_message(rendered)
        for idx, chunk in enumerate(chunks):
            ok = await self.send_private_text(user_id=user_id, text=chunk)
            if not ok:
                return False
            if idx < len(chunks) - 1 and self.send_delay_seconds > 0:
                await asyncio.sleep(self.send_delay_seconds)
        return True

    async def send_private_text(self, *, user_id: str, text: str) -> bool:
        payload: dict[str, Any] = {"message": text}
        try:
            payload["user_id"] = int(user_id)
        except (TypeError, ValueError):
            payload["user_id"] = str(user_id)

        result = await asyncio.to_thread(self._post_json, "/send_private_msg", payload)
        if not isinstance(result, dict):
            return False
        status = str(result.get("status") or "").strip().lower()
        return status == "ok"

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self._build_request_url(path)
        headers = {"Content-Type": "application/json"}
        if self.napcat_http_token is not None:
            headers["Authorization"] = f"Bearer {self.napcat_http_token}"
        req = urllib_request.Request(
            url=url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except Exception:
            return None
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _build_request_url(self, path: str) -> str:
        base_url = f"{self.napcat_http_url}{path}"
        if self.napcat_http_token is None:
            return base_url

        parsed = urllib_parse.urlsplit(base_url)
        pairs = urllib_parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "access_token" for key, _ in pairs):
            pairs.append(("access_token", self.napcat_http_token))
        query = urllib_parse.urlencode(pairs)
        return urllib_parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
        )

    def _tag_emoji(self, message_tag: str | None) -> str:
        mapping = {
            "reminder": "🔔",
            "heartbeat": "💓",
            "email_scan": "📧",
            "tool_status": "ℹ️",
        }
        return mapping.get(str(message_tag or "").strip(), "")

    def _convert_tables(self, text: str) -> str:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not (line.startswith("|") and line.endswith("|")):
                lines.append(raw_line)
                continue
            if _TABLE_ALIGN_ROW_PATTERN.match(line):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            lines.append(" | ".join(cells))
        return "\n".join(lines)
