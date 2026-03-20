from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path

import pytest

from hypo_agent.core.image_renderer import ImageRenderer


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
    template = template_path.read_text(encoding="utf-8")

    assert 'blockType === "table"' in template
    assert "target.innerHTML = window.marked.parse(String(content));" in template


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
