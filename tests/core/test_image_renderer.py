from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path

import pytest

from hypo_agent.core.image_renderer import ImageRenderError, ImageRenderer


def _set_mtime(path: Path, when: datetime) -> None:
    timestamp = when.timestamp()
    os.utime(path, (timestamp, timestamp))


def _ensure_playwright_available() -> None:
    pytest.importorskip("playwright.async_api")


def _run_renderer(tmp_path: Path, coro_factory) -> str:
    _ensure_playwright_available()

    async def _run() -> str:
        renderer = ImageRenderer(memory_dir=tmp_path / "memory")
        await renderer.initialize()
        if not renderer.available:
            pytest.skip("Playwright or Chromium unavailable")
        try:
            return await coro_factory(renderer)
        finally:
            await renderer.shutdown()

    return asyncio.run(_run())


def test_renderer_unavailable_when_playwright_start_fails(tmp_path: Path, monkeypatch) -> None:
    async def _fail_start(self):
        raise ImportError("playwright unavailable")

    monkeypatch.setattr(ImageRenderer, "_start_playwright", _fail_start)

    renderer = ImageRenderer(memory_dir=tmp_path / "memory")
    asyncio.run(renderer.initialize())

    assert renderer.available is False
    asyncio.run(renderer.shutdown())


def test_health_check_returns_false_when_unavailable(tmp_path: Path) -> None:
    renderer = ImageRenderer(memory_dir=tmp_path / "memory")

    assert asyncio.run(renderer.health_check()) is False


def test_cleanup_by_count(tmp_path: Path) -> None:
    renderer = ImageRenderer(memory_dir=tmp_path / "memory", max_retained_images=200)
    now = datetime.now()

    for index in range(201):
        path = renderer.rendered_images_dir / f"{index:03d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        _set_mtime(path, now + timedelta(seconds=index))

    renderer.cleanup_rendered_images()

    assert len(list(renderer.rendered_images_dir.glob("*.png"))) == 200
    assert (renderer.rendered_images_dir / "000.png").exists() is False
    assert (renderer.rendered_images_dir / "200.png").exists() is True


def test_cleanup_by_age(tmp_path: Path) -> None:
    renderer = ImageRenderer(memory_dir=tmp_path / "memory", retention_days=7)
    old_path = renderer.rendered_images_dir / "old.png"
    fresh_path = renderer.rendered_images_dir / "fresh.png"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"old")
    fresh_path.write_bytes(b"fresh")
    _set_mtime(old_path, datetime.now() - timedelta(days=8))

    renderer.cleanup_rendered_images()

    assert old_path.exists() is False
    assert fresh_path.exists() is True


def test_render_markdown_table(tmp_path: Path) -> None:
    output_path = _run_renderer(
        tmp_path,
        lambda renderer: renderer.render_to_image(
            "| Name | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |",
            block_type="table",
        ),
    )

    rendered = Path(output_path)
    assert rendered.exists() is True
    assert rendered.suffix == ".png"
    assert rendered.stat().st_size > 0


def test_render_template_has_explicit_table_markdown_branch() -> None:
    template_path = Path(__file__).resolve().parents[2] / "src" / "hypo_agent" / "templates" / "render.html"
    vendor_path = template_path.parent / "vendor" / "marked.min.js"
    template = template_path.read_text(encoding="utf-8")

    assert 'blockType === "table"' in template
    assert "target.innerHTML = window.marked.parse(String(content));" in template
    assert "./vendor/marked.min.js" in template
    assert "cdn.jsdelivr.net/npm/marked/marked.min.js" not in template
    assert vendor_path.exists() is True


def test_render_to_image_retries_once_after_recoverable_failure(tmp_path: Path, monkeypatch) -> None:
    renderer = ImageRenderer(memory_dir=tmp_path / "memory")
    attempts: list[str] = []
    recoveries: list[bool] = []

    async def fake_render_once(self, content: str, *, block_type: str) -> str:
        del self, content
        attempts.append(block_type)
        if len(attempts) == 1:
            raise RuntimeError("browser has been closed")
        output_path = tmp_path / "memory" / "rendered_images" / "recovered.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"png")
        return str(output_path)

    async def fake_recover(self) -> bool:
        del self
        recoveries.append(True)
        return True

    monkeypatch.setattr(ImageRenderer, "_render_to_image_once", fake_render_once)
    monkeypatch.setattr(ImageRenderer, "_recover_renderer", fake_recover)

    output_path = asyncio.run(renderer.render_to_image("A | B", block_type="table"))

    assert Path(output_path).exists() is True
    assert attempts == ["table", "table"]
    assert recoveries == [True]


def test_render_to_image_raises_fallback_text_when_retry_fails(tmp_path: Path, monkeypatch) -> None:
    renderer = ImageRenderer(memory_dir=tmp_path / "memory")

    async def fake_render_once(self, content: str, *, block_type: str) -> str:
        del self, content, block_type
        raise RuntimeError("browser has been closed")

    async def fake_recover(self) -> bool:
        del self
        return False

    monkeypatch.setattr(ImageRenderer, "_render_to_image_once", fake_render_once)
    monkeypatch.setattr(ImageRenderer, "_recover_renderer", fake_recover)

    with pytest.raises(ImageRenderError) as exc_info:
        asyncio.run(renderer.render_to_image("A | B\n--- | ---", block_type="table"))

    assert exc_info.value.block_type == "table"
    assert "[表格渲染失败，原始内容如下]" in exc_info.value.fallback_text


def test_render_code_block(tmp_path: Path) -> None:
    output_path = _run_renderer(
        tmp_path,
        lambda renderer: renderer.render_to_image(
            "def greet(name: str) -> str:\n    return f'hello {name}'\n",
            block_type="code",
        ),
    )

    rendered = Path(output_path)
    assert rendered.exists() is True
    assert rendered.stat().st_size > 0


def test_render_math(tmp_path: Path) -> None:
    output_path = _run_renderer(
        tmp_path,
        lambda renderer: renderer.render_to_image(r"$$E = mc^2$$", block_type="math"),
    )

    rendered = Path(output_path)
    assert rendered.exists() is True
    assert rendered.stat().st_size > 0


def test_render_mermaid(tmp_path: Path) -> None:
    output_path = _run_renderer(
        tmp_path,
        lambda renderer: renderer.render_to_image(
            "graph TD\nA[Start] --> B{Ready?}\nB -->|Yes| C[Done]\nB -->|No| D[Retry]",
            block_type="mermaid",
        ),
    )

    rendered = Path(output_path)
    assert rendered.exists() is True
    assert rendered.stat().st_size > 0


def test_render_to_pdf(tmp_path: Path) -> None:
    output_path = _run_renderer(
        tmp_path,
        lambda renderer: renderer.render_to_pdf("# Report\n\n- alpha\n- beta\n"),
    )

    rendered = Path(output_path)
    assert rendered.exists() is True
    assert rendered.suffix == ".pdf"
    assert rendered.stat().st_size > 0
