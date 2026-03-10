from __future__ import annotations

import inspect
from typing import Any

import structlog

from hypo_agent.channels.onebot11 import parse_onebot_private_message
from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.channels.qq")


class QQChannelService:
    def __init__(
        self,
        *,
        napcat_http_url: str,
        napcat_http_token: str | None = None,
        bot_qq: str,
        allowed_users: set[str],
        default_session_id: str = "main",
    ) -> None:
        self.adapter = QQAdapter(
            napcat_http_url=napcat_http_url,
            napcat_http_token=napcat_http_token,
        )
        self.bot_qq = str(bot_qq).strip()
        self.allowed_users = {item.strip() for item in allowed_users if item and item.strip()}
        self.default_session_id = default_session_id

    def is_allowed_user(self, user_id: str) -> bool:
        return user_id in self.allowed_users

    async def handle_onebot_event(self, payload: dict[str, Any], *, pipeline: Any) -> bool:
        parsed = parse_onebot_private_message(payload, bot_qq=self.bot_qq)
        if parsed is None:
            return False

        user_id = parsed.user_id
        if not self.is_allowed_user(user_id):
            logger.warning("qq.message.rejected", user_id=user_id, reason="not_in_whitelist")
            return False

        inbound = Message(
            text=parsed.text,
            sender="user",
            session_id=self.default_session_id,
            channel="qq",
            sender_id=user_id,
        )
        await self._run_pipeline_for_user(user_id=user_id, inbound=inbound, pipeline=pipeline)
        return True

    async def push_proactive(self, message: Message) -> None:
        await self.send_message(message)

    async def send_message(self, message: Message) -> None:
        sender_id = str(message.sender_id or "").strip()
        if sender_id and sender_id in self.allowed_users:
            target_users = [sender_id]
        else:
            target_users = sorted(self.allowed_users)
        for user_id in target_users:
            await self.adapter.send_message(user_id=user_id, message=message)

    async def _run_pipeline_for_user(self, *, user_id: str, inbound: Message, pipeline: Any) -> None:
        async def emit(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "error":
                error_message = str(event.get("message") or "处理失败，请稍后重试")
                await self.adapter.send_message(
                    user_id=user_id,
                    message=Message(
                        text=error_message,
                        sender="assistant",
                        session_id=inbound.session_id,
                        channel="qq",
                        sender_id=user_id,
                    ),
                )

        enqueue_user_message = getattr(pipeline, "enqueue_user_message", None)
        if callable(enqueue_user_message):
            result = enqueue_user_message(inbound, emit=emit)
            if inspect.isawaitable(result):
                await result
            return

        async for event in pipeline.stream_reply(inbound):
            await emit(event)
