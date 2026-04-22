from __future__ import annotations

import asyncio
import base64
import json
import hashlib
import random
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote
from uuid import uuid4

import httpx
import structlog

from hypo_agent.exceptions import ChannelError, ExternalServiceError

logger = structlog.get_logger("hypo_agent.channels.weixin.ilink_client")

_USE_STORED_TOKEN = object()
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_LOGIN_POLL_INTERVAL_SECONDS = 1.0
_LOGIN_TOTAL_TIMEOUT_SECONDS = 300.0
_LOGIN_MAX_REFRESHES = 3
_CHANNEL_VERSION = "1.0.2"
_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


class ILinkError(ExternalServiceError):
    """Base exception for iLink client failures."""


class ILinkAPIError(ILinkError):
    """Raised when the iLink API returns an application-level error."""

    def __init__(self, path: str, response: dict[str, Any], message: str | None = None) -> None:
        self.path = path
        self.response = response
        errcode = response.get("errcode")
        ret = response.get("ret")
        detail = str(
            response.get("errmsg")
            or response.get("message")
            or response.get("msg")
            or message
            or "unknown error"
        )
        super().__init__(f"{path} failed: ret={ret!r} errcode={errcode!r} detail={detail}")


class LoginError(ILinkError):
    """Raised when the QR-code login flow fails."""


class SessionExpiredError(ChannelError, ILinkAPIError):
    """Raised when the remote bot session has expired and login is required."""


class ILinkClient:
    def __init__(
        self,
        base_url: str,
        token_path: str = "memory/weixin_auth.json",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_path = Path(token_path).expanduser()
        self.bot_token: str | None = None
        self.bot_id = ""
        self.user_id = ""
        self.last_context_token = ""
        self.get_updates_buf = ""
        self._sleep = sleep_func
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=40.0, write=10.0, pool=10.0),
            transport=transport,
        )
        self._load_state()

    async def login(
        self,
        on_qrcode_content: Callable[[str], None] | None = None,
    ) -> dict[str, str]:
        qr_payload = await self._request_get(
            "/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
        )
        qrcode_id, qrcode_content = self._parse_qrcode_payload(qr_payload)

        if on_qrcode_content is not None:
            on_qrcode_content(qrcode_content)

        refresh_count = 0
        deadline = time.monotonic() + _LOGIN_TOTAL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status_payload = await self._request_get(
                "/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode_id},
            )
            status = str(status_payload.get("status") or "").strip().lower()
            logger.info("weixin.login.status", status=status)

            if status == "wait":
                await self._sleep(_LOGIN_POLL_INTERVAL_SECONDS)
                continue

            if status == "scaned":
                logger.info("weixin.login.scaned")
                await self._sleep(_LOGIN_POLL_INTERVAL_SECONDS)
                continue

            if status == "expired":
                refresh_count += 1
                if refresh_count > _LOGIN_MAX_REFRESHES:
                    raise LoginError("二维码多次过期")
                logger.info("weixin.login.qr_expired", refresh=refresh_count)
                qr_payload = await self._request_get(
                    "/ilink/bot/get_bot_qrcode",
                    params={"bot_type": "3"},
                )
                qrcode_id, qrcode_content = self._parse_qrcode_payload(qr_payload)
                if on_qrcode_content is not None:
                    on_qrcode_content(qrcode_content)
                await self._sleep(_LOGIN_POLL_INTERVAL_SECONDS)
                continue

            if status == "confirmed":
                token = str(status_payload.get("bot_token") or "").strip()
                if not token:
                    raise LoginError("登录成功响应缺少 bot_token")
                session = {
                    "token": token,
                    "baseurl": str(status_payload.get("baseurl") or self.base_url).strip()
                    or self.base_url,
                    "bot_id": str(status_payload.get("ilink_bot_id") or "").strip(),
                    "user_id": str(status_payload.get("ilink_user_id") or "").strip(),
                }
                self.bot_token = session["token"]
                self.base_url = session["baseurl"]
                self.bot_id = session["bot_id"]
                self.user_id = session["user_id"]
                self.last_context_token = ""
                self.get_updates_buf = ""
                self._persist_state()
                return session

            raise LoginError(f"未知二维码状态: {status or '<empty>'}")

        raise LoginError("登录超时")

    async def get_updates(self) -> list[dict[str, Any]]:
        self._require_bot_token()
        payload = await self._request_post(
            "/ilink/bot/getupdates",
            {"get_updates_buf": self.get_updates_buf},
        )
        next_cursor = payload.get("get_updates_buf")
        if isinstance(next_cursor, str):
            self.get_updates_buf = next_cursor
            self._persist_state()

        raw_messages = payload.get("msgs")
        if not isinstance(raw_messages, list):
            return []
        return [
            message
            for message in raw_messages
            if isinstance(message, dict) and int(message.get("message_type") or 0) == 1
        ]

    async def send_message(
        self,
        to_user_id: str,
        text: str,
        context_token: str | None = "",
        *,
        msg_id: str | None = None,
        message_state: int = 2,
    ) -> str:
        client_id = msg_id or f"wcb-{uuid4()}"
        await self.send_message_raw(
            to_user_id=to_user_id,
            text=text,
            context_token=context_token,
            client_id=client_id,
            msg_id=msg_id,
            message_state=message_state,
        )
        return client_id

    async def send_message_raw(
        self,
        *,
        to_user_id: str,
        text: str | None = None,
        item_list: list[dict[str, Any]] | None = None,
        context_token: str | None = "",
        client_id: str | None = None,
        msg_id: str | None = None,
        message_state: int = 2,
    ) -> dict[str, Any]:
        self._require_bot_token()
        resolved_client_id = client_id or f"wcb-{uuid4()}"
        resolved_item_list = list(item_list or [])
        if not resolved_item_list:
            resolved_item_list = [
                {
                    "type": 1,
                    "text_item": {
                        "text": str(text or ""),
                    },
                }
            ]
        message_payload: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": resolved_client_id,
            "message_type": 2,
            "message_state": int(message_state),
            "item_list": resolved_item_list,
        }
        if context_token is not None:
            message_payload["context_token"] = context_token
        if msg_id:
            message_payload["msg_id"] = msg_id
        return await self._request_post("/ilink/bot/sendmessage", {"msg": message_payload})

    async def get_upload_url(
        self,
        *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey: str,
        no_need_thumb: bool = True,
    ) -> dict[str, Any]:
        self._require_bot_token()
        return await self._request_post(
            "/ilink/bot/getuploadurl",
            {
                "filekey": str(filekey).strip(),
                "media_type": int(media_type),
                "to_user_id": str(to_user_id).strip(),
                "rawsize": int(rawsize),
                "rawfilemd5": str(rawfilemd5).strip(),
                "filesize": int(filesize),
                "no_need_thumb": bool(no_need_thumb),
                "aeskey": str(aeskey).strip(),
            },
        )

    async def upload_media(self, *, upload_param: str, filekey: str, encrypted_data: bytes) -> str:
        upload_url = (
            f"{_CDN_BASE_URL}/upload"
            f"?encrypted_query_param={quote(str(upload_param).strip(), safe='')}"
            f"&filekey={quote(str(filekey).strip(), safe='')}"
        )
        response = await self._client.post(
            upload_url,
            content=bytes(encrypted_data),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(encrypted_data)),
            },
        )
        response.raise_for_status()
        encrypted_param = str(response.headers.get("x-encrypted-param") or "").strip()
        if not encrypted_param:
            raise ILinkError("CDN upload succeeded but x-encrypted-param header is missing")
        return encrypted_param

    async def send_image(
        self,
        to_user_id: str,
        encrypt_query_param: str,
        aes_key: str,
        encrypted_file_size: int,
        *,
        context_token: str | None = "",
        msg_id: str | None = None,
        message_state: int = 2,
        caption: str | None = None,
    ) -> dict[str, Any]:
        self._require_bot_token()
        client_id = msg_id or f"wcb-{uuid4()}"
        item_list: list[dict[str, Any]] = []
        if str(caption or "").strip():
            item_list.append(
                {
                    "type": 1,
                    "text_item": {
                        "text": str(caption).strip(),
                    },
                }
            )
        item_list.append(
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "encrypt_query_param": str(encrypt_query_param).strip(),
                        "aes_key": str(aes_key).strip(),
                        "encrypt_type": 1,
                    },
                    "mid_size": int(encrypted_file_size),
                },
            }
        )
        message_payload: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": int(message_state),
            "item_list": item_list,
        }
        if context_token is not None:
            message_payload["context_token"] = context_token
        if msg_id:
            message_payload["msg_id"] = msg_id
        return await self._request_post("/ilink/bot/sendmessage", {"msg": message_payload})

    @staticmethod
    def build_media_upload_payload(
        *,
        to_user_id: str,
        media_type: int,
        plaintext: bytes,
        encrypted_size: int,
        aes_key_hex: str,
    ) -> dict[str, Any]:
        filekey = f"{random.getrandbits(128):032x}"
        return {
            "filekey": filekey,
            "media_type": int(media_type),
            "to_user_id": str(to_user_id).strip(),
            "rawsize": len(plaintext),
            "rawfilemd5": hashlib.md5(plaintext).hexdigest(),
            "filesize": int(encrypted_size),
            "no_need_thumb": True,
            "aeskey": str(aes_key_hex).strip(),
        }

    async def send_typing(self, user_id: str, status: int = 1) -> None:
        config = await self.get_config(user_id)
        typing_ticket = str(config.get("typing_ticket") or "").strip()
        if not typing_ticket:
            raise ILinkAPIError(
                "/ilink/bot/getconfig",
                config,
                message="missing typing_ticket",
            )
        await self._request_post(
            "/ilink/bot/sendtyping",
            {
                "ilink_user_id": user_id,
                "typing_ticket": typing_ticket,
                "status": int(status),
            },
        )

    async def get_config(self, user_id: str, context_token: str = "") -> dict[str, Any]:
        self._require_bot_token()
        payload: dict[str, Any] = {"ilink_user_id": user_id}
        if context_token:
            payload["context_token"] = context_token
        return await self._request_post("/ilink/bot/getconfig", payload)

    async def download_media(self, url: str) -> bytes:
        response = await self._client.get(str(url).strip())
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()

    def remember_user_id(self, user_id: str) -> None:
        normalized = str(user_id or "").strip()
        if not normalized or normalized == self.user_id:
            return
        self.user_id = normalized
        self._persist_state()

    def remember_context_token(self, context_token: str) -> None:
        normalized = str(context_token or "").strip()
        if not normalized or normalized == self.last_context_token:
            return
        self.last_context_token = normalized
        self._persist_state()

    async def _request_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.get(self._build_url(path), params=params)
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise LoginError(f"{path} 返回了非对象 JSON")
        self._log_api_call("GET", path, parsed)
        self._raise_if_error(path, parsed, login_error=True)
        return parsed

    async def _request_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        token: object | str | None = _USE_STORED_TOKEN,
    ) -> dict[str, Any]:
        auth_token = self.bot_token if token is _USE_STORED_TOKEN else token
        request_payload = dict(payload)
        request_payload["base_info"] = {"channel_version": _CHANNEL_VERSION}
        body_str = json.dumps(request_payload, ensure_ascii=False)
        body_bytes = body_str.encode("utf-8")

        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            try:
                response = await self._client.post(
                    self._build_url(path),
                    content=body_bytes,
                    headers=self._build_headers(auth_token, len(body_bytes)),
                )
                response.raise_for_status()
                parsed = response.json()
                if not isinstance(parsed, dict):
                    raise ILinkAPIError(path, {"message": "response payload is not an object"})
                self._log_api_call("POST", path, parsed)
                self._raise_if_error(path, parsed, login_error=False)
                return parsed
            except SessionExpiredError:
                raise
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt >= len(_RETRY_BACKOFF_SECONDS):
                    logger.error(
                        "weixin.api.network_error",
                        path=path,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    raise
                backoff_seconds = _RETRY_BACKOFF_SECONDS[attempt]
                logger.warning(
                    "weixin.api.retry",
                    path=path,
                    attempt=attempt + 1,
                    backoff_seconds=backoff_seconds,
                    error=str(exc),
                )
                await self._sleep(backoff_seconds)
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "weixin.api.http_error",
                    path=path,
                    status_code=exc.response.status_code,
                )
                raise

        raise AssertionError("unreachable")

    def _load_state(self) -> None:
        if not self.token_path.exists():
            return
        try:
            payload = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("weixin.auth.load_failed", token_path=str(self.token_path))
            return
        if not isinstance(payload, dict):
            logger.warning("weixin.auth.invalid_payload", token_path=str(self.token_path))
            return

        token = payload.get("bot_token")
        if not isinstance(token, str) or not token.strip():
            token = payload.get("token")
        if isinstance(token, str) and token.strip():
            self.bot_token = token.strip()

        baseurl = payload.get("baseurl")
        if isinstance(baseurl, str) and baseurl.strip():
            self.base_url = baseurl.strip().rstrip("/")

        bot_id = payload.get("bot_id")
        if isinstance(bot_id, str):
            self.bot_id = bot_id.strip()

        user_id = payload.get("user_id")
        if isinstance(user_id, str):
            self.user_id = user_id.strip()

        cursor = payload.get("get_updates_buf")
        if isinstance(cursor, str):
            self.get_updates_buf = cursor

        context_token = payload.get("last_context_token")
        if isinstance(context_token, str):
            self.last_context_token = context_token.strip()

    def _persist_state(self) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": self.bot_token or "",
            "bot_token": self.bot_token or "",
            "baseurl": self.base_url,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "last_context_token": self.last_context_token,
            "get_updates_buf": self.get_updates_buf,
        }
        self.token_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _invalidate_session(self) -> None:
        self.bot_token = None
        self.last_context_token = ""
        self.get_updates_buf = ""
        self._persist_state()

    def _require_bot_token(self) -> None:
        if self.bot_token:
            return
        raise ILinkError("bot_token is missing, call login() first")

    def _parse_qrcode_payload(self, payload: dict[str, Any]) -> tuple[str, str]:
        qrcode_id = str(payload.get("qrcode") or "").strip()
        qrcode_content = str(payload.get("qrcode_img_content") or "").strip()
        if not qrcode_id or not qrcode_content:
            raise LoginError("二维码响应缺少 qrcode 或 qrcode_img_content")
        return qrcode_id, qrcode_content

    def _build_url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _build_headers(self, token: str | None, content_length: int) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._random_uin(),
            "Content-Length": str(content_length),
        }
        if token and str(token).strip():
            headers["Authorization"] = f"Bearer {str(token).strip()}"
        return headers

    def _log_api_call(self, method: str, path: str, payload: dict[str, Any]) -> None:
        logger.info(
            "weixin.api.call",
            method=method,
            path=path,
            ret=payload.get("ret"),
            errcode=payload.get("errcode"),
        )

    def _raise_if_error(self, path: str, payload: dict[str, Any], *, login_error: bool) -> None:
        errcode = payload.get("errcode")
        ret = payload.get("ret")
        if errcode == -14:
            self._invalidate_session()
            raise SessionExpiredError(path, payload, message="session expired")
        if isinstance(errcode, int) and errcode != 0:
            if login_error:
                raise LoginError(str(ILinkAPIError(path, payload)))
            raise ILinkAPIError(path, payload)
        if isinstance(ret, int) and ret != 0:
            if login_error:
                raise LoginError(str(ILinkAPIError(path, payload)))
            raise ILinkAPIError(path, payload)

    def _random_uin(self) -> str:
        raw = str(random.randint(0, 2**32 - 1)).encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")
