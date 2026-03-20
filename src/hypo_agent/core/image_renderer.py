from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from hypo_agent.core.config_loader import get_memory_dir

logger = structlog.get_logger("hypo_agent.core.image_renderer")


class ImageRenderer:
    def __init__(
        self,
        *,
        memory_dir: Path | str | None = None,
        template_path: Path | str | None = None,
        viewport: dict[str, int] | None = None,
        retention_days: int = 7,
        max_retained_images: int = 200,
    ) -> None:
        self.memory_dir = (
            Path(memory_dir).expanduser().resolve(strict=False)
            if memory_dir is not None
            else get_memory_dir().resolve(strict=False)
        )
        self.rendered_images_dir = (self.memory_dir / "rendered_images").resolve(strict=False)
        self.exports_dir = (self.memory_dir / "exports").resolve(strict=False)
        self.rendered_images_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self._template_path = (
            Path(template_path).expanduser().resolve(strict=False)
            if template_path is not None
            else (Path(__file__).resolve().parents[1] / "templates" / "render.html")
        )
        self._viewport = viewport or {"width": 800, "height": 600}
        self._retention_days = max(1, int(retention_days))
        self._max_retained_images = max(1, int(max_retained_images))
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def _start_playwright(self) -> Any:
        from playwright.async_api import async_playwright

        return await async_playwright().start()

    async def initialize(self) -> None:
        if self._context is not None and self._browser is not None:
            self._available = True
            return

        try:
            self._playwright = await self._start_playwright()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport=self._viewport,
                color_scheme="light",
                device_scale_factor=2,
            )
        except Exception as exc:
            self._available = False
            logger.warning("image_renderer.unavailable", error=str(exc))
            await self.shutdown()
            return

        self._available = True
        logger.info(
            "image_renderer.initialized",
            template_path=str(self._template_path),
            rendered_images_dir=str(self.rendered_images_dir),
        )

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        page = await self._prepare_page()
        capture_page: Any | None = None
        output_path = self._build_output_path(self.rendered_images_dir, suffix=".png")

        try:
            await self._render(page, content=content, block_type=block_type)
            snapshot_html = await self._prepare_for_capture(page)
            capture_page = await self._build_capture_page(snapshot_html)
            element = await capture_page.query_selector("#render-target")
            if element is None:
                raise RuntimeError("render target not found")
            await element.screenshot(path=str(output_path))
        finally:
            if capture_page is not None:
                await capture_page.close()
            await page.close()

        self.cleanup_rendered_images()
        return str(output_path)

    async def render_to_pdf(self, content: str) -> str:
        page = await self._prepare_page()
        capture_page: Any | None = None
        output_path = self._build_output_path(self.exports_dir, suffix=".pdf")

        try:
            await self._render(page, content=content, block_type="markdown")
            snapshot_html = await self._prepare_for_capture(page)
            capture_page = await self._build_capture_page(snapshot_html)
            await capture_page.emulate_media(media="screen")
            await capture_page.pdf(
                path=str(output_path),
                print_background=True,
                width="800px",
                margin={
                    "top": "24px",
                    "right": "24px",
                    "bottom": "24px",
                    "left": "24px",
                },
            )
        finally:
            if capture_page is not None:
                await capture_page.close()
            await page.close()

        return str(output_path)

    def cleanup_rendered_images(self) -> None:
        if not self.rendered_images_dir.exists():
            return

        cutoff = datetime.now() - timedelta(days=self._retention_days)
        for path in list(self.rendered_images_dir.glob("*.png")):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue

        remaining = sorted(
            [path for path in self.rendered_images_dir.glob("*.png") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in remaining[self._max_retained_images :]:
            stale.unlink(missing_ok=True)

    async def shutdown(self) -> None:
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._context = None
        self._browser = None
        self._playwright = None
        self._available = False

        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    async def _prepare_page(self) -> Any:
        if not self.available or self._context is None:
            raise RuntimeError("ImageRenderer is unavailable")
        if not self._template_path.exists():
            raise FileNotFoundError(self._template_path)

        page = await self._context.new_page()
        await page.route("**/*", self._route_request)
        await page.goto(self._template_path.resolve(strict=False).as_uri(), wait_until="domcontentloaded")
        await page.wait_for_function("() => typeof window.renderContent === 'function'")
        return page

    async def _render(self, page: Any, *, content: str, block_type: str) -> None:
        await page.evaluate(
            """
            async ({ content, blockType }) => {
                await window.renderContent(content, blockType);
            }
            """,
            {"content": content, "blockType": block_type},
        )
        await page.wait_for_function("() => document.body.dataset.ready === 'true'")

    def _build_output_path(self, directory: Path, *, suffix: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return (directory / f"{stamp}_{uuid4().hex}{suffix}").resolve(strict=False)

    async def _route_request(self, route: Any) -> None:
        request = route.request
        if request.resource_type == "font":
            await route.abort()
            return
        await route.continue_()

    async def _prepare_for_capture(self, page: Any) -> str:
        snapshot = await page.evaluate(
            """
            () => {
                const styles = Array.from(document.querySelectorAll("style"))
                    .map((node) => node.innerHTML)
                    .join("\\n");
                const target = document.getElementById("render-target");
                return {
                    styles,
                    html: target ? target.outerHTML : "<div id='render-target'></div>",
                };
            }
            """
        )
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{snapshot['styles']}</style>"
            "</head><body>"
            f"{snapshot['html']}"
            "</body></html>"
        )
        return html

    async def _build_capture_page(self, html: str) -> Any:
        if self._context is None:
            raise RuntimeError("ImageRenderer is unavailable")
        page = await self._context.new_page()
        await page.set_content(html, wait_until="domcontentloaded")
        return page
