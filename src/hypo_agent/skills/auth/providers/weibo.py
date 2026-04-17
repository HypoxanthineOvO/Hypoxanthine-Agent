from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from http.cookies import SimpleCookie

from hypo_agent.skills.auth.errors import CookieExtractionError, QrCodeExpiredError
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)
_WEIBO_QR_IMAGE_URL = "https://passport.weibo.com/sso/v2/qrcode/image"
_WEIBO_QR_CHECK_URL = "https://passport.weibo.com/sso/v2/qrcode/check"
_WEIBO_QR_LOGIN_URL = "https://passport.weibo.com/sso/v2/login"
_WEIBO_CONFIG_URL = "https://m.weibo.cn/api/config"


class WeiboProvider:
    platform = "weibo"

    def __init__(self, *, http_transport: Any | None = None) -> None:
        self.http_transport = http_transport

    def supports_cookie_import(self) -> bool:
        return True

    async def start(self, ctx: AuthContext) -> LoginActionResult:
        async with self._client() as client:
            response = await client.get(_WEIBO_QR_IMAGE_URL, params={"entry": "miniblog", "size": "180"})
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if int(payload.get("retcode", -1) or -1) != 20000000 or not isinstance(data, dict):
            raise ValueError(f"微博二维码生成失败：{payload.get('msg') or '未知错误'}")
        qrid = str(data.get("qrid") or "").strip()
        image_url = str(data.get("image") or "").strip()
        attachment = await ctx.build_qr_attachment("weibo", image_url)
        text = "请用微博 App 扫描二维码并确认登录，然后告诉我“扫了”或“登录了”。"
        if attachment is None:
            text += f"\n二维码链接：{image_url}"
        return LoginActionResult(
            text=text,
            attachments=[attachment] if attachment is not None else [],
            pending=PendingLogin(
                platform=self.platform,
                provider_key=self.platform,
                backend_key="api",
                session_id=ctx.session_id,
                created_at=ctx.now_fn().isoformat(),
                expires_at=(ctx.now_fn() + timedelta(seconds=180)).isoformat(),
                payload={"qrid": qrid, "image_url": image_url},
            ),
        )

    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        qrid = str(pending.payload.get("qrid") or "").strip()
        async with self._client() as client:
            last_message = "还没有扫码，请先扫描上面的二维码。"
            for attempt in range(ctx.auth_check_poll_attempts):
                response = await client.get(_WEIBO_QR_CHECK_URL, params={"entry": "miniblog", "qrid": qrid})
                response.raise_for_status()
                payload = response.json()
                retcode = int(payload.get("retcode", -1) or -1)
                data = payload.get("data") if isinstance(payload, dict) else None
                if retcode == 50114001:
                    last_message = "还没有扫码，请先扫描上面的二维码。"
                elif retcode == 50114002:
                    last_message = "已扫码，请在手机上点击确认。"
                elif retcode == 50114004:
                    raise QrCodeExpiredError("二维码已过期，请重新调用 auth_login。")
                elif retcode == 20000000 and isinstance(data, dict) and str(data.get("alt") or "").strip():
                    login = await client.get(
                        _WEIBO_QR_LOGIN_URL,
                        params={"entry": "miniblog", "returntype": "META", "alt": str(data.get("alt") or "")},
                    )
                    login.raise_for_status()
                    cookie_str = self._extract_cookie_string(login)
                    if not cookie_str:
                        raise CookieExtractionError("微博登录成功，但未拿到完整 Cookie")
                    return LoginActionResult(
                        text="✅ 微博登录成功！Cookie 已更新。",
                        status="success",
                        cookie=cookie_str,
                        clear_pending=True,
                    )
                else:
                    message = str(payload.get("msg") or payload.get("message") or "未知错误").strip()
                    return LoginActionResult(text=f"微博登录状态异常：{message}", status="error")
                if attempt + 1 < ctx.auth_check_poll_attempts:
                    await ctx.sleep_func(ctx.auth_check_poll_interval_seconds)
            return LoginActionResult(text=last_message, status="timeout")

    async def verify(self, ctx: AuthContext, pending: PendingLogin | None, code: str) -> LoginActionResult:
        del ctx, pending, code
        return LoginActionResult(text="微博当前走扫码登录，不需要验证码。请先调用 auth_login，再在扫码后调用 auth_check。")

    async def status(self, ctx: AuthContext) -> str:
        cookie = ctx.get_cookie(self.platform)
        if not cookie:
            return "📱 微博: ⚠️ 未登录"
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://m.weibo.cn/",
                "Accept": "application/json, text/plain, */*",
            },
            cookies={part.split("=", 1)[0]: part.split("=", 1)[1] for part in cookie.split("; ") if "=" in part},
            timeout=_HTTP_TIMEOUT,
            transport=self.http_transport,
            follow_redirects=True,
        ) as client:
            response = await client.get(_WEIBO_CONFIG_URL)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        username = ""
        if isinstance(data, dict):
            username = str(data.get("screen_name") or data.get("nick") or data.get("name") or "").strip()
        if isinstance(data, dict) and int(payload.get("ok", 0) or 0) == 1 and (
            data.get("login") is True or bool(data.get("uid")) or bool(username)
        ):
            return f"📱 微博: ✅ 有效（用户: {username}）" if username else "📱 微博: ✅ 有效"
        return "📱 微博: ❌ 已失效（说“帮我登录微博”可以重新登录）"

    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None:
        del ctx, pending
        return None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://weibo.com/",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=_HTTP_TIMEOUT,
            transport=self.http_transport,
            follow_redirects=True,
        )

    def _extract_cookie_string(self, response: httpx.Response) -> str:
        cookie_pairs: list[str] = []
        for raw_header in response.headers.get_list("set-cookie"):
            chunks = raw_header.split(", ")
            for chunk in chunks:
                simple = SimpleCookie()
                try:
                    simple.load(chunk)
                except Exception:
                    continue
                for key, morsel in simple.items():
                    value = str(morsel.value).strip()
                    if key and value:
                        cookie_pairs.append(f"{key}={value}")
        return "; ".join(dict.fromkeys(cookie_pairs))
