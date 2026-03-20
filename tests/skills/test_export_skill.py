from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.skills.export_skill import ExportSkill


class StubRenderer:
    def __init__(self, *, root: Path, available: bool = True) -> None:
        self.available = available
        self.root = root
        self.exports_dir = root / "exports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.rendered_images_dir = root / "rendered_images"
        self.rendered_images_dir.mkdir(parents=True, exist_ok=True)

    async def render_to_pdf(self, content: str) -> str:
        path = self.exports_dir / "raw.pdf"
        path.write_bytes(f"pdf:{content}".encode("utf-8"))
        return str(path)

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        path = self.rendered_images_dir / f"{block_type}.png"
        path.write_bytes(f"png:{content}".encode("utf-8"))
        return str(path)


def test_export_markdown(tmp_path: Path) -> None:
    skill = ExportSkill(image_renderer=StubRenderer(root=tmp_path))

    output = asyncio.run(skill.export_to_file("# 标题\n", format="markdown", filename="notes"))

    assert output.status == "success"
    target = Path(str(output.result))
    assert target.exists() is True
    assert target.read_text(encoding="utf-8") == "# 标题\n"
    assert output.attachments[0].filename.endswith(".md")


def test_export_pdf(tmp_path: Path) -> None:
    skill = ExportSkill(image_renderer=StubRenderer(root=tmp_path))

    output = asyncio.run(skill.export_to_file("# 标题\n", format="pdf", filename="report"))

    assert output.status == "success"
    target = Path(str(output.result))
    assert target.exists() is True
    assert target.suffix == ".pdf"
    assert target.stat().st_size > 0


def test_export_pdf_renderer_unavailable(tmp_path: Path) -> None:
    skill = ExportSkill(image_renderer=StubRenderer(root=tmp_path, available=False))

    output = asyncio.run(skill.export_to_file("# 标题\n", format="pdf", filename="report"))

    assert output.status == "error"
    assert "无法生成 PDF" in output.error_info


def test_render_to_image(tmp_path: Path) -> None:
    skill = ExportSkill(image_renderer=StubRenderer(root=tmp_path))

    output = asyncio.run(skill.render_content_to_image("# 标题\n"))

    assert output.status == "success"
    target = Path(str(output.result))
    assert target.exists() is True
    assert output.attachments[0].type == "image"
    assert output.attachments[0].url == str(target)


def test_skill_registration(tmp_path: Path) -> None:
    manager = SkillManager()
    manager.register(ExportSkill(image_renderer=StubRenderer(root=tmp_path)))

    tool_names = {tool["function"]["name"] for tool in manager.get_tools_schema()}

    assert "export_to_file" in tool_names
    assert "render_content_to_image" in tool_names
