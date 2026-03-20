from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.core.uploads import sanitize_upload_filename
from hypo_agent.models import Attachment, SkillOutput
from hypo_agent.skills.base import BaseSkill


class ExportSkill(BaseSkill):
    """Export content to Markdown, PDF, or rendered images."""

    name = "export"
    description = "Export content to Markdown files, PDF files, or rendered images."
    required_permissions: list[str] = []

    def __init__(self, *, image_renderer: Any) -> None:
        self._renderer = image_renderer
        exports_dir = getattr(image_renderer, "exports_dir", get_memory_dir() / "exports")
        self._exports_dir = Path(exports_dir).expanduser().resolve(strict=False)
        self._exports_dir.mkdir(parents=True, exist_ok=True)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "export_to_file",
                    "description": (
                        "将内容导出为文件（Markdown 或 PDF），用于用户说“整理成文件给我”、"
                        "“导出 PDF”、“保存成文档”等场景。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Markdown content to export."},
                            "format": {
                                "type": "string",
                                "enum": ["markdown", "pdf"],
                                "description": "Export format.",
                            },
                            "filename": {
                                "type": "string",
                                "description": "Output filename without extension.",
                            },
                        },
                        "required": ["content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "render_content_to_image",
                    "description": (
                        "将内容渲染为图片，用于用户说“截图给我”、“转成图片”、"
                        "“渲染成图片”等场景。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Markdown content to render."},
                        },
                        "required": ["content"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "export_to_file":
            return await self.export_to_file(
                content=str(params.get("content") or ""),
                format=str(params.get("format") or "markdown"),
                filename=str(params.get("filename") or "export"),
            )
        if tool_name == "render_content_to_image":
            return await self.render_content_to_image(
                content=str(params.get("content") or "")
            )
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}' for export skill")

    async def export_to_file(
        self,
        content: str,
        format: str = "markdown",
        filename: str = "export",
    ) -> SkillOutput:
        normalized_content = str(content or "")
        if not normalized_content.strip():
            return SkillOutput(status="error", error_info="content is required")

        normalized_format = str(format or "markdown").strip().lower()
        safe_stem = self._safe_stem(filename)
        if normalized_format == "markdown":
            target_path = self._build_export_path(safe_stem, ".md")
            target_path.write_text(normalized_content, encoding="utf-8")
            attachment = self._build_attachment(
                target_path,
                attachment_type="file",
                mime_type="text/markdown",
            )
            return SkillOutput(
                status="success",
                result=str(target_path),
                metadata={"format": "markdown"},
                attachments=[attachment],
            )

        if normalized_format == "pdf":
            if not getattr(self._renderer, "available", False):
                return SkillOutput(
                    status="error",
                    error_info="图片渲染引擎不可用，无法生成 PDF",
                )
            generated_path = Path(await self._renderer.render_to_pdf(normalized_content))
            target_path = self._build_export_path(safe_stem, ".pdf")
            generated_path.replace(target_path)
            attachment = self._build_attachment(
                target_path,
                attachment_type="file",
                mime_type="application/pdf",
            )
            return SkillOutput(
                status="success",
                result=str(target_path),
                metadata={"format": "pdf"},
                attachments=[attachment],
            )

        return SkillOutput(status="error", error_info=f"Unsupported export format '{normalized_format}'")

    async def render_content_to_image(self, content: str) -> SkillOutput:
        normalized_content = str(content or "")
        if not normalized_content.strip():
            return SkillOutput(status="error", error_info="content is required")
        if not getattr(self._renderer, "available", False):
            return SkillOutput(
                status="error",
                error_info="图片渲染引擎不可用，无法生成图片",
            )

        image_path = Path(await self._renderer.render_to_image(normalized_content))
        attachment = self._build_attachment(
            image_path,
            attachment_type="image",
            mime_type="image/png",
        )
        return SkillOutput(
            status="success",
            result=str(image_path),
            metadata={"format": "png"},
            attachments=[attachment],
        )

    def _safe_stem(self, filename: str) -> str:
        cleaned = sanitize_upload_filename(filename or "export")
        stem = Path(cleaned).stem.strip() or "export"
        return stem

    def _build_export_path(self, stem: str, suffix: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return (self._exports_dir / f"{stamp}_{stem}{suffix}").resolve(strict=False)

    def _build_attachment(
        self,
        path: Path,
        *,
        attachment_type: str,
        mime_type: str,
    ) -> Attachment:
        resolved = path.expanduser().resolve(strict=False)
        return Attachment(
            type=attachment_type,
            url=str(resolved),
            filename=resolved.name,
            mime_type=mime_type,
            size_bytes=resolved.stat().st_size,
        )
