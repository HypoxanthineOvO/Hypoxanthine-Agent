from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from hypo_agent.skills.auth.errors import LoginTimeoutError, QrCodeExpiredError, RiskControlError
from hypo_agent.skills.auth.playwright_engine import PlaywrightLoginEngine
from hypo_agent.skills.auth.platform_configs import PLAYWRIGHT_PLATFORM_CONFIGS
from hypo_agent.skills.auth.providers.playwright_qr import result_session
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)
_ZHIHU_UDID_URL = "https://www.zhihu.com/udid"
_ZHIHU_QR_CREATE_URL = "https://www.zhihu.com/api/v3/account/api/login/qrcode"
_ZHIHU_ME_URL = "https://www.zhihu.com/api/v4/me"


class ZhihuProvider:
    platform = "zhihu"

    def __init__(self, *, http_transport: Any | None = None) -> None:
        self.http_transport = http_transport

    def supports_cookie_import(self) -> bool:
        return True

    async def start(self, ctx: AuthContext) -> LoginActionResult:
        try:
            return await self._start_api(ctx)
        except RiskControlError:
            return await self._start_playwright(ctx, switched=False)

    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        if pending.backend_key == "playwright":
            return await self._check_playwright(ctx, pending)
        try:
            return await self._check_api(ctx, pending)
        except RiskControlError:
            return await self._start_playwright(ctx, switched=True)

    async def verify(self, ctx: AuthContext, pending: PendingLogin | None, code: str) -> LoginActionResult:
        del ctx, pending, code
        return LoginActionResult(text="知乎当前优先走扫码登录，不需要手动输入验证码。请先调用 auth_login，再在扫码后调用 auth_check。")

    async def status(self, ctx: AuthContext) -> str:
        cookie = ctx.get_cookie(self.platform)
        if not cookie:
            return "📖 知乎: ⚠️ 未登录"
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.zhihu.com/",
                "Accept": "application/json, text/plain, */*",
            },
            cookies={part.split("=", 1)[0]: part.split("=", 1)[1] for part in cookie.split("; ") if "=" in part},
            timeout=_HTTP_TIMEOUT,
            transport=self.http_transport,
            follow_redirects=True,
        ) as client:
            response = await client.get(_ZHIHU_ME_URL)
        if response.status_code in {401, 403}:
            return "📖 知乎: ❌ 已失效（说“帮我登录知乎”可以重新登录）"
        response.raise_for_status()
        payload = response.json()
        username = str((payload or {}).get("name") or (payload or {}).get("fullname") or "").strip()
        return f"📖 知乎: ✅ 有效（用户: {username}）" if username else "📖 知乎: ✅ 有效"

    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None:
        if pending.backend_key != "playwright":
            return
        context_id = str(pending.payload.get("context_id") or "").strip()
        if not context_id or ctx.playwright_runtime is None:
            return
        close_context = getattr(ctx.playwright_runtime, "close_context", None)
        if callable(close_context):
            await close_context(context_id)

    async def _start_api(self, ctx: AuthContext) -> LoginActionResult:
        async with self._client() as client:
            udid = await client.post(_ZHIHU_UDID_URL)
            udid.raise_for_status()
            response = await client.post(_ZHIHU_QR_CREATE_URL)
            if self._is_risk_response(response):
                raise RiskControlError("知乎触发风控，切换浏览器登录。")
            response.raise_for_status()
            payload = response.json()
        token = str(payload.get("token") or "").strip()
        link = str(payload.get("link") or "").strip()
        expires_at = payload.get("expires_at")
        attachment = await ctx.build_qr_attachment("zhihu", link)
        expires_iso = (ctx.now_fn() + timedelta(seconds=60)).isoformat()
        if expires_at:
            try:
                expires_iso = datetime_from_timestamp(int(expires_at)).isoformat()
            except (TypeError, ValueError):
                pass
        text = "请用知乎 App 扫描二维码并确认登录，然后告诉我“扫了”或“登录了”。"
        if attachment is None:
            text += f"\n二维码链接：{link}"
        return LoginActionResult(
            text=text,
            attachments=[attachment] if attachment is not None else [],
            pending=PendingLogin(
                platform=self.platform,
                provider_key=self.platform,
                backend_key="api",
                session_id=ctx.session_id,
                created_at=ctx.now_fn().isoformat(),
                expires_at=expires_iso,
                payload={"token": token, "link": link},
            ),
        )

    async def _check_api(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        token = str(pending.payload.get("token") or "").strip()
        async with self._client() as client:
            last_message = "还没有完成扫码或确认，请先扫码后再试。"
            for attempt in range(ctx.auth_check_poll_attempts):
                response = await client.get(f"{_ZHIHU_QR_CREATE_URL}/{token}/scan_info")
                if self._is_risk_response(response):
                    raise RiskControlError("知乎扫码轮询触发风控，切换浏览器登录。")
                if response.status_code == 404:
                    raise QrCodeExpiredError("二维码已过期，请重新调用 auth_login。")
                response.raise_for_status()
                payload = response.json() if response.content else {}
                cookies = client.cookies
                if cookies.get("z_c0"):
                    cookie_parts = []
                    for key in ("z_c0", "SESSIONID", "_xsrf", "d_c0"):
                        value = cookies.get(key)
                        if value:
                            cookie_parts.append(f"{key}={value}")
                    return LoginActionResult(
                        text="✅ 知乎登录成功！Cookie 已更新。",
                        status="success",
                        cookie="; ".join(cookie_parts),
                        clear_pending=True,
                    )
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "").strip().lower()
                    if status in {"0", "waiting", "scan"}:
                        last_message = "还没有扫码，请先扫描上面的二维码。"
                    elif status in {"1", "scanned", "confirm"}:
                        last_message = "已扫码，请在手机上点击确认。"
                if attempt + 1 < ctx.auth_check_poll_attempts:
                    await ctx.sleep_func(ctx.auth_check_poll_interval_seconds)
            return LoginActionResult(text=last_message, status="timeout")

    async def _start_playwright(self, ctx: AuthContext, *, switched: bool) -> LoginActionResult:
        engine = PlaywrightLoginEngine(runtime=ctx.playwright_runtime, qr_dir=ctx.qr_dir, now_fn=ctx.now_fn)
        result = await engine.start(PLAYWRIGHT_PLATFORM_CONFIGS["zhihu"])
        text = "已自动切换到浏览器二维码，请扫描新二维码。" if switched else "请扫描知乎二维码完成登录，扫完后再让我检查登录状态。"
        return LoginActionResult(
            text=text,
            attachments=[result.attachment],
            pending=PendingLogin(
                platform=self.platform,
                provider_key=self.platform,
                backend_key="playwright",
                session_id=ctx.session_id,
                created_at=result.session.created_at,
                expires_at=(ctx.now_fn() + timedelta(seconds=PLAYWRIGHT_PLATFORM_CONFIGS["zhihu"].login_wait_seconds)).isoformat(),
                payload={"context_id": result.session.context_id},
            ),
        )

    async def _check_playwright(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        engine = PlaywrightLoginEngine(runtime=ctx.playwright_runtime, qr_dir=ctx.qr_dir, now_fn=ctx.now_fn)
        try:
            cookie = await engine.check(
                PLAYWRIGHT_PLATFORM_CONFIGS["zhihu"],
                result_session(self.platform, pending),
            )
        except LoginTimeoutError as exc:
            return LoginActionResult(text=str(exc), status="timeout")
        return LoginActionResult(
            text="✅ 知乎登录成功！Cookie 已更新。",
            status="success",
            cookie=cookie,
            clear_pending=True,
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.zhihu.com/signin",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=_HTTP_TIMEOUT,
            transport=self.http_transport,
            follow_redirects=True,
        )

    def _is_risk_response(self, response: httpx.Response) -> bool:
        if response.status_code != 403:
            return False
        try:
            payload = response.json()
        except Exception:
            return False
        error = payload.get("error") if isinstance(payload, dict) else None
        return int((error or {}).get("code", 0) or 0) == 40352


def datetime_from_timestamp(value: int):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, UTC)
