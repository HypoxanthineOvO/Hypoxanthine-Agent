from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import qrcode

from hypo_agent.channels.info.wewe_rss_client import WeWeRSSAuthError, WeWeRSSClientError
from hypo_agent.models import Attachment, Message


class WeWeRSSMonitorService:
    ALERT_SIGNATURE_KEY = "wewe_rss.last_alert_signature"
    LAST_LOGIN_UUID_KEY = "wewe_rss.last_login_uuid"
    LAST_LOGIN_STARTED_AT_KEY = "wewe_rss.last_login_started_at"

    def __init__(
        self,
        *,
        client: Any,
        structured_store: Any,
        event_queue: Any,
        qr_dir: Path | str,
        default_session_id: str = "main",
        sleep_func=asyncio.sleep,
        login_timeout_seconds: int = 180,
        poll_interval_seconds: int = 3,
    ) -> None:
        self.client = client
        self.structured_store = structured_store
        self.event_queue = event_queue
        self.qr_dir = Path(qr_dir).resolve(strict=False)
        self.default_session_id = str(default_session_id or "main")
        self._sleep = sleep_func
        self.login_timeout_seconds = max(10, int(login_timeout_seconds))
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._active_login_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def is_login_request(self, text: str | None) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        exact_matches = {
            "我扫码登录一下",
            "我扫码登录下",
            "发我二维码",
            "发一下二维码",
            "给我二维码",
        }
        if normalized in exact_matches:
            return True
        if "扫码登录" in normalized:
            return True
        if "二维码" in normalized and any(token in normalized for token in ("wewe", "读书", "rss", "登录")):
            return True
        return False

    async def check_accounts(self) -> dict[str, Any]:
        try:
            payload = await self.client.list_accounts()
        except WeWeRSSAuthError:
            summary = "WeWe RSS 鉴权失败：authCode 无效或已过期。"
            return await self._emit_alert(summary=summary, signature="auth_error")
        except WeWeRSSClientError as exc:
            summary = f"WeWe RSS 巡检失败：{exc}"
            return await self._emit_alert(summary=summary, signature=f"client_error:{exc}")

        items = payload.get("items") if isinstance(payload, dict) else None
        normalized_items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        invalid_items = [item for item in normalized_items if int(item.get("status") or 0) == 0]
        if not normalized_items:
            return await self._emit_alert(
                summary="WeWe RSS 当前没有已接入的微信读书账号，请重新扫码登录。",
                signature="no_accounts",
            )
        if invalid_items:
            names = [str(item.get("name") or item.get("id") or "unknown").strip() for item in invalid_items]
            summary = "WeWe RSS 微信读书账号已失效：" + "、".join(name for name in names if name)
            signature = "invalid:" + ",".join(
                sorted(str(item.get("id") or item.get("name") or "").strip() for item in invalid_items)
            )
            return await self._emit_alert(summary=summary, signature=signature)

        await self.structured_store.delete_preference(self.ALERT_SIGNATURE_KEY)
        return {"status": "healthy", "invalid_count": 0}

    async def start_login_flow(
        self,
        *,
        session_id: str,
        channel: str,
        sender_id: str | None = None,
    ) -> Message:
        try:
            payload = await self.client.create_login_url()
        except WeWeRSSAuthError:
            return self._error_message(
                text="WeWe RSS 无法生成二维码：authCode 无效或已过期。",
                session_id=session_id,
                channel=channel,
                sender_id=sender_id,
            )
        except WeWeRSSClientError as exc:
            return self._error_message(
                text=f"WeWe RSS 无法生成二维码：{exc}",
                session_id=session_id,
                channel=channel,
                sender_id=sender_id,
            )

        login_id = str(payload.get("uuid") or "").strip()
        scan_url = str(payload.get("scanUrl") or "").strip()
        if not login_id or not scan_url:
            return self._error_message(
                text="WeWe RSS 返回的二维码信息不完整，请稍后重试。",
                session_id=session_id,
                channel=channel,
                sender_id=sender_id,
            )

        attachment = await self._build_qr_attachment(login_id=login_id, scan_url=scan_url)
        await self.structured_store.set_preference(self.LAST_LOGIN_UUID_KEY, login_id)
        await self.structured_store.set_preference(self.LAST_LOGIN_STARTED_AT_KEY, login_id)

        task_key = (str(session_id or self.default_session_id), str(channel or "webui"))
        previous = self._active_login_tasks.pop(task_key, None)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            self._poll_login_result(
                login_id=login_id,
                session_id=str(session_id or self.default_session_id),
                channel=str(channel or "webui"),
            )
        )
        self._active_login_tasks[task_key] = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(lambda _: self._active_login_tasks.pop(task_key, None))

        return Message(
            text="请扫码登录 WeWe RSS 微信读书账号。二维码已发送，扫码后我会继续通知登录结果。",
            attachments=[attachment],
            sender="assistant",
            session_id=str(session_id or self.default_session_id),
            channel=str(channel or "webui"),
            sender_id=sender_id,
            message_tag="tool_status",
            metadata={"target_channels": [str(channel or "webui")]},
        )

    async def wait_for_background_tasks(self) -> None:
        if not self._background_tasks:
            return
        await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        close = getattr(self.client, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result

    async def _emit_alert(self, *, summary: str, signature: str) -> dict[str, Any]:
        previous_signature = await self.structured_store.get_preference(self.ALERT_SIGNATURE_KEY)
        if previous_signature == signature:
            return {"status": "deduped", "summary": summary}
        await self.structured_store.set_preference(self.ALERT_SIGNATURE_KEY, signature)
        await self.event_queue.put(
            {
                "event_type": "wewe_rss_trigger",
                "session_id": self.default_session_id,
                "summary": summary,
                "channel": "all",
            }
        )
        return {"status": "alerted", "summary": summary}

    async def _build_qr_attachment(self, *, login_id: str, scan_url: str) -> Attachment:
        self.qr_dir.mkdir(parents=True, exist_ok=True)
        image = qrcode.make(scan_url)
        path = self.qr_dir / f"wewe-rss-{login_id}.png"
        image.save(path)
        return Attachment(
            type="image",
            url=str(path.resolve(strict=False)),
            filename=path.name,
            mime_type="image/png",
            size_bytes=path.stat().st_size,
        )

    async def _poll_login_result(
        self,
        *,
        login_id: str,
        session_id: str,
        channel: str,
    ) -> None:
        attempts = max(1, self.login_timeout_seconds // self.poll_interval_seconds)
        for attempt in range(attempts):
            try:
                payload = await self.client.get_login_result(login_id)
            except WeWeRSSClientError as exc:
                await self._enqueue_status(
                    session_id=session_id,
                    channel=channel,
                    summary=f"WeWe RSS 登录结果查询失败：{exc}",
                )
                return

            vid = str(payload.get("vid") or "").strip()
            username = str(payload.get("username") or "").strip()
            token = str(payload.get("token") or "").strip()
            if vid and token:
                try:
                    await self.client.add_account(id=vid, name=username or vid, token=token)
                except WeWeRSSClientError as exc:
                    await self._enqueue_status(
                        session_id=session_id,
                        channel=channel,
                        summary=f"WeWe RSS 扫码成功，但写入账号失败：{exc}",
                    )
                    return
                await self.structured_store.delete_preference(self.ALERT_SIGNATURE_KEY)
                await self._enqueue_status(
                    session_id=session_id,
                    channel=channel,
                    summary=f"WeWe RSS 账号已恢复：{username or vid}。",
                )
                return

            message = str(payload.get("message") or "").strip()
            if message:
                await self._enqueue_status(
                    session_id=session_id,
                    channel=channel,
                    summary=f"WeWe RSS 登录失败：{message}",
                )
                return

            if attempt + 1 < attempts:
                await self._sleep(float(self.poll_interval_seconds))

        await self._enqueue_status(
            session_id=session_id,
            channel=channel,
            summary="WeWe RSS 登录超时，请重新获取二维码后再试。",
        )

    async def _enqueue_status(self, *, session_id: str, channel: str, summary: str) -> None:
        await self.event_queue.put(
            {
                "event_type": "wewe_rss_trigger",
                "session_id": session_id,
                "summary": summary,
                "channel": channel,
            }
        )

    def _error_message(
        self,
        *,
        text: str,
        session_id: str,
        channel: str,
        sender_id: str | None,
    ) -> Message:
        return Message(
            text=text,
            sender="assistant",
            session_id=str(session_id or self.default_session_id),
            channel=str(channel or "webui"),
            sender_id=sender_id,
            message_tag="tool_status",
            metadata={"target_channels": [str(channel or "webui")]},
        )
