from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger("hypo_agent.skills.auth.playwright_runtime")


class PlaywrightRuntime:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        launch_args: list[str] | None = None,
    ) -> None:
        self.user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        )
        self.launch_args = launch_args or ["--disable-blink-features=AutomationControlled"]
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._available = False
        self._sessions: dict[str, Any] = {}
        self._counter = 0

    @property
    def available(self) -> bool:
        return self._available

    async def initialize(self) -> None:
        if self._browser is not None:
            self._available = True
            return
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True, args=self.launch_args)
            self._available = True
        except Exception as exc:  # pragma: no cover - runtime availability
            self._available = False
            logger.warning("auth.playwright.unavailable", error=str(exc))
            await self.shutdown()

    async def new_context(self) -> tuple[str, Any]:
        await self.initialize()
        if not self.available or self._browser is None:
            raise RuntimeError("Chromium 未安装或 Playwright 不可用，无法发起浏览器登录")
        self._counter += 1
        context_id = f"context-{self._counter}"
        context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1440, "height": 1200},
            color_scheme="light",
        )
        await context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined,
});
"""
        )
        self._sessions[context_id] = context
        return context_id, context

    async def close_context(self, context_id: str) -> None:
        context = self._sessions.pop(context_id, None)
        if context is not None:
            await context.close()

    async def shutdown(self) -> None:
        for context_id in list(self._sessions.keys()):
            await self.close_context(context_id)
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        self._available = False
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
