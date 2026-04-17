from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from hypo_agent.skills.auth.errors import QrCodeExpiredError
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)
_BILIBILI_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
_BILIBILI_QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
_BILIBILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def _extract_bilibili_cookie(payload: dict[str, Any]) -> str:
    redirect_url = str(payload.get("url") or payload.get("redirect_url") or "").strip()
    if redirect_url.startswith("https://www.bilibili.com/?"):
        pairs = redirect_url.split("?", 1)[1].split("&")
        values = {}
        for pair in pairs:
            if "=" in pair:
                key, value = pair.split("=", 1)
                values[key] = value
        extracted = [f"{key}={values[key]}" for key in ("SESSDATA", "DedeUserID", "bili_jct") if values.get(key)]
        return "; ".join(extracted)
    return ""


class BilibiliProvider:
    platform = "bilibili"

    def __init__(self, *, http_transport: Any | None = None) -> None:
        self.http_transport = http_transport

    def supports_cookie_import(self) -> bool:
        return True

    async def start(self, ctx: AuthContext) -> LoginActionResult:
        async with self._client() as client:
            response = await client.get(_BILIBILI_QR_GENERATE_URL)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or int(payload.get("code", -1)) != 0:
            raise ValueError("B站二维码生成失败")
        scan_url = str(data.get("url") or "").strip()
        qrcode_key = str(data.get("qrcode_key") or "").strip()
        attachment = await ctx.build_qr_attachment("bilibili", scan_url)
        text = "请用B站 APP 扫描这个二维码，然后告诉我“扫了”或“登录了”。"
        if attachment is None:
            text += f"\n二维码链接：{scan_url}"
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
                payload={"qrcode_key": qrcode_key, "scan_url": scan_url},
            ),
        )

    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        qrcode_key = str(pending.payload.get("qrcode_key") or "").strip()
        async with self._client() as client:
            last_message = "还没有扫码，请先扫描上面的二维码。"
            for attempt in range(ctx.auth_check_poll_attempts):
                response = await client.get(_BILIBILI_QR_POLL_URL, params={"qrcode_key": qrcode_key})
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    raise ValueError("B站登录状态返回异常")
                poll_code = int(data.get("code", payload.get("code", -1)))
                if poll_code == 0:
                    cookie_str = _extract_bilibili_cookie(data)
                    if not cookie_str:
                        raise ValueError("B站登录成功，但未拿到完整 Cookie")
                    return LoginActionResult(
                        text="✅ B站登录成功！Cookie 已更新。",
                        status="success",
                        cookie=cookie_str,
                        clear_pending=True,
                    )
                if poll_code == 86038:
                    raise QrCodeExpiredError("二维码已过期，请重新调用 auth_login。")
                if poll_code == 86090:
                    last_message = "已扫码，请在手机上点击确认。"
                elif poll_code == 86101:
                    last_message = "还没有扫码，请先扫描上面的二维码。"
                else:
                    message = str(data.get("message") or payload.get("message") or "未知错误").strip()
                    return LoginActionResult(text=f"B站登录状态异常：{message}", status="error")
                if attempt + 1 < ctx.auth_check_poll_attempts:
                    await ctx.sleep_func(ctx.auth_check_poll_interval_seconds)
            return LoginActionResult(text=last_message, status="timeout")

    async def verify(self, ctx: AuthContext, pending: PendingLogin | None, code: str) -> LoginActionResult:
        del ctx, pending, code
        return LoginActionResult(text="B站 当前走扫码登录，不需要验证码。请先调用 auth_login，再在扫码后调用 auth_check。")

    async def status(self, ctx: AuthContext) -> str:
        cookie = ctx.get_cookie(self.platform)
        if not cookie:
            return "📺 B站: ⚠️ 未登录"
        async with self._client(cookie=cookie) as client:
            response = await client.get(_BILIBILI_NAV_URL)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and int(payload.get("code", 0) or 0) == 0 and bool(data.get("isLogin")):
            username = str(data.get("uname") or "").strip()
            return f"📺 B站: ✅ 有效（用户: {username}）" if username else "📺 B站: ✅ 有效"
        return "📺 B站: ❌ 已失效（说“帮我登录B站”可以重新登录）"

    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None:
        del ctx, pending
        return None

    def _client(self, *, cookie: str | None = None) -> httpx.AsyncClient:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        }
        if cookie:
            headers["Cookie"] = cookie
        return httpx.AsyncClient(
            headers=headers,
            timeout=_HTTP_TIMEOUT,
            transport=self.http_transport,
            follow_redirects=True,
        )
