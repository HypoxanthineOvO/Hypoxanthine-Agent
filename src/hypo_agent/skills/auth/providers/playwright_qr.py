from __future__ import annotations

from datetime import timedelta

from hypo_agent.skills.auth.playwright_engine import PlaywrightLoginEngine
from hypo_agent.skills.auth.platform_configs import PLAYWRIGHT_PLATFORM_CONFIGS
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin


class PlaywrightQrProvider:
    def __init__(self, *, platform: str) -> None:
        self.platform = platform

    def supports_cookie_import(self) -> bool:
        return True

    def _config(self):
        return PLAYWRIGHT_PLATFORM_CONFIGS[self.platform]

    async def start(self, ctx: AuthContext) -> LoginActionResult:
        engine = PlaywrightLoginEngine(runtime=ctx.playwright_runtime, qr_dir=ctx.qr_dir, now_fn=ctx.now_fn)
        result = await engine.start(self._config())
        return LoginActionResult(
            text=f"请扫描{self._display_name()}二维码完成登录，扫完后再让我检查登录状态。",
            attachments=[result.attachment],
            pending=PendingLogin(
                platform=self.platform,
                provider_key=self.platform,
                backend_key="playwright",
                session_id=ctx.session_id,
                created_at=result.session.created_at,
                expires_at=(ctx.now_fn() + timedelta(seconds=self._config().login_wait_seconds)).isoformat(),
                payload={"context_id": result.session.context_id},
            ),
        )

    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        engine = PlaywrightLoginEngine(runtime=ctx.playwright_runtime, qr_dir=ctx.qr_dir, now_fn=ctx.now_fn)
        cookie = await engine.check(
            self._config(),
            session=result_session(self.platform, pending),
        )
        return LoginActionResult(
            text=f"✅ {self._display_name()}登录成功！Cookie 已更新。",
            status="success",
            cookie=cookie,
            clear_pending=True,
        )

    async def verify(self, ctx: AuthContext, pending: PendingLogin | None, code: str) -> LoginActionResult:
        del ctx, pending, code
        return LoginActionResult(text=f"{self._display_name()}当前走扫码登录，不需要验证码。请先调用 auth_login，再在扫码后调用 auth_check。")

    async def status(self, ctx: AuthContext) -> str:
        cookie = ctx.get_cookie(self.platform)
        if not cookie:
            return f"{self._status_prefix()}: ⚠️ 未登录"
        return f"{self._status_prefix()}: ✅ 已配置"

    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None:
        context_id = str(pending.payload.get("context_id") or "").strip()
        if not context_id or ctx.playwright_runtime is None:
            return
        close_context = getattr(ctx.playwright_runtime, "close_context", None)
        if callable(close_context):
            await close_context(context_id)

    def _display_name(self) -> str:
        return "微信读书" if self.platform == "weread" else self.platform

    def _status_prefix(self) -> str:
        return "📚 微信读书" if self.platform == "weread" else self.platform


def result_session(platform: str, pending: PendingLogin):
    from hypo_agent.skills.auth.types import BrowserLoginSession

    return BrowserLoginSession(
        context_id=str(pending.payload.get("context_id") or ""),
        platform=platform,
        created_at=str(pending.created_at or ""),
    )
