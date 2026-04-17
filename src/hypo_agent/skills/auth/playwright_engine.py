from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypo_agent.models import Attachment
from hypo_agent.skills.auth.errors import BrowserUnavailableError, LoginTimeoutError, RiskControlError
from hypo_agent.skills.auth.types import BrowserLoginSession, PlaywrightPlatformConfig


@dataclass
class EngineStartResult:
    session: BrowserLoginSession
    attachment: Attachment


class PlaywrightLoginEngine:
    def __init__(self, *, runtime: Any, qr_dir: Path, now_fn: Any) -> None:
        self.runtime = runtime
        self.qr_dir = qr_dir
        self.now_fn = now_fn

    async def start(self, config: PlaywrightPlatformConfig) -> EngineStartResult:
        await self.runtime.initialize()
        if not getattr(self.runtime, "available", False):
            raise BrowserUnavailableError("Chromium 未安装或 Playwright 不可用，无法发起浏览器登录")
        context_id, context = await self.runtime.new_context()
        page = await context.new_page()
        try:
            await page.goto(config.login_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            if "ERR_NETWORK_CHANGED" not in str(exc):
                raise
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if callable(wait_for_timeout):
                await wait_for_timeout(2000)
            await page.goto(config.login_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_load_state = getattr(page, "wait_for_load_state", None)
        if callable(wait_for_load_state):
            try:
                await wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        for action in config.entry_actions:
            if str(action.get("kind") or "") == "click_text":
                locator = page.get_by_text(
                    str(action.get("text") or ""),
                    exact=bool(action.get("exact")),
                )
                await locator.click(timeout=10000)
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            await wait_for_timeout(2000)
        attachment = await self._capture_qr_attachment(page=page, config=config)
        return EngineStartResult(
            session=BrowserLoginSession(
                context_id=context_id,
                platform=config.platform,
                created_at=self.now_fn().isoformat(),
            ),
            attachment=attachment,
        )

    async def check(self, config: PlaywrightPlatformConfig, session: BrowserLoginSession) -> str:
        context = getattr(self.runtime, "contexts", None)
        if isinstance(context, dict):
            browser_context = context.get(session.context_id)
        else:
            browser_context = None
        if browser_context is None and hasattr(self.runtime, "_sessions"):
            browser_context = getattr(self.runtime, "_sessions").get(session.context_id)
        if browser_context is None:
            raise LoginTimeoutError("浏览器登录会话已失效，请重新发起登录。")
        cookies = await browser_context.cookies()
        cookie_str = self._cookies_to_string(config=config, cookies=cookies)
        if cookie_str:
            return cookie_str
        raise LoginTimeoutError("还没有完成扫码或确认，请先扫码后再试。")

    async def cleanup(self, session: BrowserLoginSession) -> None:
        close_context = getattr(self.runtime, "close_context", None)
        if callable(close_context):
            await close_context(session.context_id)

    async def _capture_qr_attachment(self, *, page: Any, config: PlaywrightPlatformConfig) -> Attachment:
        self.qr_dir.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for target in config.qr_targets:
            if str(target.get("kind") or "") != "locator":
                continue
            selector = str(target.get("selector") or "").strip()
            if not selector:
                continue
            try:
                locator = page.locator(selector)
                await locator.wait_for(state="visible", timeout=max(1000, config.qr_wait_seconds * 1000))
                path = self.qr_dir / f"{config.platform}_qr_{int(self.now_fn().timestamp())}.png"
                await locator.screenshot(path=str(path))
                return Attachment(
                    type="image",
                    url=str(path.resolve(strict=False)),
                    filename=path.name,
                    mime_type="image/png",
                    size_bytes=path.stat().st_size,
                )
            except Exception as exc:  # pragma: no cover - selector variations
                last_error = exc
                continue
        if last_error is not None:
            raise RiskControlError(f"{config.platform} 登录页未找到可用二维码：{last_error}")
        raise RiskControlError(f"{config.platform} 登录页未找到可用二维码")

    def _cookies_to_string(self, *, config: PlaywrightPlatformConfig, cookies: list[dict[str, Any]]) -> str:
        accepted: list[str] = []
        required_names = {name for name in config.success_cookies if name}
        domain_suffixes = [suffix for suffix in config.cookie_domains if suffix]
        for item in cookies:
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            domain = str(item.get("domain") or "").strip()
            if not name or not value:
                continue
            if required_names and name in required_names:
                accepted.append(f"{name}={value}")
                continue
            if any(domain.endswith(suffix) or domain == suffix.lstrip(".") for suffix in domain_suffixes):
                accepted.append(f"{name}={value}")
        if required_names and not any(part.split("=", 1)[0] in required_names for part in accepted):
            return ""
        return "; ".join(dict.fromkeys(accepted))
