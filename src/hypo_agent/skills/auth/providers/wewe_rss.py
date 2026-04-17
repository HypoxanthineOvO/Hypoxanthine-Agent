from __future__ import annotations

from datetime import timedelta

from hypo_agent.channels.info.wewe_rss_client import WeWeRSSAuthError, WeWeRSSClientError
from hypo_agent.skills.auth.types import AuthContext, LoginActionResult, PendingLogin


class WeWeRSSProvider:
    platform = "wewe_rss"

    def supports_cookie_import(self) -> bool:
        return False

    async def start(self, ctx: AuthContext) -> LoginActionResult:
        client = ctx.build_wewe_client()
        try:
            payload = await client.create_login_url()
        finally:
            await ctx.close_client(client)
        login_id = str(payload.get("uuid") or "").strip()
        scan_url = str(payload.get("scanUrl") or "").strip()
        attachment = await ctx.build_qr_attachment("wewe_rss", scan_url)
        text = "请扫码登录 WeWe RSS 微信读书账号，扫完后再让我检查登录状态。"
        if attachment is None:
            text += f"\n二维码链接：{scan_url}"
        return LoginActionResult(
            text=text,
            attachments=[attachment] if attachment is not None else [],
            pending=PendingLogin(
                platform=self.platform,
                provider_key=self.platform,
                backend_key="wewe_client",
                session_id=ctx.session_id,
                created_at=ctx.now_fn().isoformat(),
                expires_at=(ctx.now_fn() + timedelta(seconds=180)).isoformat(),
                payload={"login_id": login_id, "scan_url": scan_url},
            ),
        )

    async def check(self, ctx: AuthContext, pending: PendingLogin) -> LoginActionResult:
        client = ctx.build_wewe_client()
        try:
            last_message = "还没有完成扫码或确认，请先扫码后再试。"
            for attempt in range(ctx.auth_check_poll_attempts):
                payload = await client.get_login_result(str(pending.payload.get("login_id") or ""))
                vid = str(payload.get("vid") or "").strip()
                token = str(payload.get("token") or "").strip()
                username = str(payload.get("username") or vid).strip()
                if vid and token:
                    await client.add_account(id=vid, name=username or vid, token=token)
                    return LoginActionResult(
                        text="✅ WeWe RSS 登录成功，账号已恢复。",
                        status="success",
                        clear_pending=True,
                    )
                message = str(payload.get("message") or "").strip()
                if message:
                    return LoginActionResult(text=f"WeWe RSS 登录失败：{message}", status="error")
                if attempt + 1 < ctx.auth_check_poll_attempts:
                    await ctx.sleep_func(ctx.auth_check_poll_interval_seconds)
            return LoginActionResult(text=last_message, status="timeout")
        finally:
            await ctx.close_client(client)

    async def verify(self, ctx: AuthContext, pending: PendingLogin | None, code: str) -> LoginActionResult:
        del ctx, pending, code
        return LoginActionResult(text="WeWe RSS 当前走扫码登录，不需要验证码。请先调用 auth_login，再在扫码后调用 auth_check。")

    async def status(self, ctx: AuthContext) -> str:
        client = ctx.build_wewe_client()
        try:
            payload = await client.list_accounts()
        except WeWeRSSAuthError:
            return "📚 WeWe RSS: ❌ 已失效（authCode 无效）"
        except WeWeRSSClientError as exc:
            return f"📚 WeWe RSS: ❌ 检查失败（{exc}）"
        finally:
            await ctx.close_client(client)
        items = payload.get("items") if isinstance(payload, dict) else None
        account_count = len([item for item in items if isinstance(item, dict)]) if isinstance(items, list) else 0
        return f"📚 WeWe RSS: ✅ 已配置（{account_count} 个账号）" if account_count > 0 else "📚 WeWe RSS: ✅ 已配置"

    async def cleanup(self, ctx: AuthContext, pending: PendingLogin) -> None:
        del ctx, pending
        return None
