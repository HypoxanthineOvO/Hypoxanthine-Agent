"""ImageGenSkill — generate and edit images via GPT-Image-2 (C2 M1+M2).

Wraps the local `gpt_image_cli` CLI to provide text-to-image generation
and image editing as registered SkillManager tools.  Returns generated
images as `Attachment(type="image")` for channel delivery.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from hypo_agent.models import Attachment, SkillOutput
from hypo_agent.skills.base import BaseSkill

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2
_RETRY_BACKOFF = (2.0, 4.0)  # seconds
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "medium"
_DEFAULT_N = 1
_DEFAULT_OUTPUT_DIR = "output/imagegen"

# CLI binary name — resolved at call time so PATH changes are picked up.
_CLI_BIN = "gpt_image_cli"
_SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _download_url(url: str, dest_dir: Path) -> Path | None:
    """Download an image from *url* to *dest_dir*. Returns the local path or None on failure."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)
        # Determine filename from URL or use a default
        filename = Path(parsed.path).name or "downloaded.png"
        if not any(filename.lower().endswith(ext) for ext in _SUPPORTED_IMAGE_EXTENSIONS):
            filename += ".png"
        dest = dest_dir / filename

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                dest.write_bytes(data)
                return dest
    except Exception:
        return None


async def _run_cli(cmd: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
    """Run *cmd* asynchronously and return ``(returncode, stdout, stderr)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, "", "Connection timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:  # pragma: no cover — defensive
        return -1, "", str(exc)


def _classify_error(rc: int, stderr: str) -> dict[str, Any]:
    """Return a structured error classification from CLI output."""
    lower = (stderr or "").lower()

    if rc == -1 and ("not found" in lower or "command not found" in lower):
        return {
            "error_type": "cli_not_found",
            "retryable": False,
            "recovery_action": {
                "type": "setup_required",
                "reason": "cli_not_found",
                "message": "gpt_image_cli is not installed. Run `gpt_image_cli setup` first.",
            },
        }

    if "content" in lower and ("policy" in lower or "violation" in lower or "flagged" in lower):
        return {
            "error_type": "content_policy",
            "retryable": False,
            "recovery_action": {
                "type": "modify_prompt",
                "reason": "content_policy_violation",
                "message": "Your prompt was rejected by the content filter. Please rephrase your request.",
            },
        }

    if "timed out" in lower or "timeout" in lower:
        return {
            "error_type": "network_timeout",
            "retryable": True,
            "recovery_action": {
                "type": "retry",
                "reason": "network_timeout",
                "message": "Image generation timed out. Retrying...",
            },
        }

    if "rate" in lower and ("limit" in lower or "429" in lower):
        return {
            "error_type": "api_rate_limit",
            "retryable": True,
            "recovery_action": {
                "type": "retry",
                "reason": "api_rate_limit",
                "message": "API rate limit reached. Retrying after backoff...",
            },
        }

    if "invalid" in lower or "error: argument" in lower:
        return {
            "error_type": "invalid_params",
            "retryable": False,
            "recovery_action": {
                "type": "report_error",
                "reason": "invalid_params",
                "message": f"Invalid parameters: {stderr[:200]}",
            },
        }

    # Default: unknown error, not retryable
    return {
        "error_type": "api_error",
        "retryable": False,
        "recovery_action": {
            "type": "report_error",
            "reason": "api_error",
            "message": f"Image generation failed: {stderr[:200]}",
        },
    }


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

class ImageGenSkill(BaseSkill):
    """Generate images via GPT-Image-2 CLI wrapper."""

    name = "image_gen"
    description = (
        "Generate images from text descriptions using GPT-Image-2. "
        "Supports style, negative prompts, and multiple image generation."
    )
    required_permissions: list[str] = []

    def __init__(self, *, output_dir: Path | str | None = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else Path(_DEFAULT_OUTPUT_DIR)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Tool schema
    # ------------------------------------------------------------------

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_image",
                    "description": (
                        "Generate an image from a text description using GPT-Image-2. "
                        "Returns the generated image as an attachment for channel delivery."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Text description of the image to generate.",
                            },
                            "size": {
                                "type": "string",
                                "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                                "default": _DEFAULT_SIZE,
                                "description": "Image dimensions.",
                            },
                            "quality": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "auto"],
                                "default": _DEFAULT_QUALITY,
                                "description": "Image quality level.",
                            },
                            "n": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4,
                                "default": _DEFAULT_N,
                                "description": "Number of images to generate.",
                            },
                            "style": {
                                "type": "string",
                                "description": "Artistic style (e.g. 'watercolor', 'photorealistic', 'anime').",
                            },
                            "negative": {
                                "type": "string",
                                "description": "Elements to exclude from the generated image.",
                            },
                        },
                        "required": ["prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_image",
                    "description": (
                        "Edit an existing image using GPT-Image-2. "
                        "Supports style transfer, element addition/removal, and inpainting. "
                        "Accepts local file paths or URLs as input."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_url": {
                                "type": "string",
                                "description": "Path or URL of the source image to edit.",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Description of the desired edit or transformation.",
                            },
                            "mask_url": {
                                "type": "string",
                                "description": "Path or URL of a mask image for inpainting (white = edit area).",
                            },
                            "input_fidelity": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                                "default": "medium",
                                "description": "How closely to follow the original image.",
                            },
                            "size": {
                                "type": "string",
                                "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                                "default": "auto",
                            },
                            "quality": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "auto"],
                                "default": _DEFAULT_QUALITY,
                            },
                            "n": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4,
                                "default": _DEFAULT_N,
                            },
                        },
                        "required": ["image_url", "prompt"],
                    },
                },
            },
        ]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "generate_image":
            return await self._generate_image(
                prompt=str(params.get("prompt", "")),
                size=str(params.get("size") or _DEFAULT_SIZE),
                quality=str(params.get("quality") or _DEFAULT_QUALITY),
                n=int(params.get("n") or _DEFAULT_N),
                style=params.get("style"),
                negative=params.get("negative"),
            )
        if tool_name == "edit_image":
            return await self._edit_image(
                image_url=str(params.get("image_url", "")),
                prompt=str(params.get("prompt", "")),
                mask_url=params.get("mask_url"),
                input_fidelity=str(params.get("input_fidelity") or "medium"),
                size=str(params.get("size") or "auto"),
                quality=str(params.get("quality") or _DEFAULT_QUALITY),
                n=int(params.get("n") or _DEFAULT_N),
            )
        return SkillOutput(
            status="error",
            error_info=f"Unknown tool '{tool_name}'",
        )

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def _build_generate_command(
        self,
        *,
        prompt: str,
        size: str = _DEFAULT_SIZE,
        quality: str = _DEFAULT_QUALITY,
        n: int = _DEFAULT_N,
        style: str | None = None,
        negative: str | None = None,
        out_path: str = "",
    ) -> list[str]:
        """Build the ``gpt_image_cli generate`` command list."""
        cmd = [
            _CLI_BIN, "generate",
            "--model", "gpt-image-2",
            "--prompt", prompt,
            "--size", size,
            "--quality", quality,
            "--n", str(n),
        ]
        if style:
            cmd.extend(["--style", style])
        if negative:
            cmd.extend(["--negative", negative])
        if out_path:
            cmd.extend(["--out", out_path])
        return cmd

    def _build_edit_command(
        self,
        *,
        prompt: str,
        image_path: str,
        mask_path: str | None = None,
        input_fidelity: str = "medium",
        size: str = "auto",
        quality: str = _DEFAULT_QUALITY,
        n: int = _DEFAULT_N,
        out_path: str = "",
    ) -> list[str]:
        """Build the ``gpt_image_cli edit`` command list."""
        cmd = [
            _CLI_BIN, "edit",
            "--model", "gpt-image-2",
            "--prompt", prompt,
            "--image", image_path,
            "--input-fidelity", input_fidelity,
            "--size", size,
            "--quality", quality,
            "--n", str(n),
        ]
        if mask_path:
            cmd.extend(["--mask", mask_path])
        if out_path:
            cmd.extend(["--out", out_path])
        return cmd

    # ------------------------------------------------------------------
    # URL resolution
    # ------------------------------------------------------------------

    async def _resolve_image_path(
        self,
        image_url: str,
        tmp_dir: Path,
    ) -> Path | None:
        """Resolve *image_url* to a local file path.

        If *image_url* is a URL, download it.  If it's a local path, validate
        it exists.  Returns ``None`` on failure.
        """
        parsed = urlparse(image_url)
        if parsed.scheme in ("http", "https"):
            downloaded = await _download_url(image_url, tmp_dir)
            return downloaded

        # Treat as local path
        path = Path(image_url)
        if path.exists():
            return path
        return None

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def _generate_image(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        n: int,
        style: str | None,
        negative: str | None,
    ) -> SkillOutput:
        """Generate images with retry logic."""
        if not prompt.strip():
            return SkillOutput(
                status="error",
                error_info="prompt is required and must not be empty.",
                metadata={"error_type": "invalid_params", "retryable": False},
            )

        # Generate output filename
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        out_path = self._output_dir / f"img_{ts}.png"

        cmd = self._build_generate_command(
            prompt=prompt,
            size=size,
            quality=quality,
            n=n,
            style=style,
            negative=negative,
            out_path=str(out_path),
        )

        last_error: dict[str, Any] = {}
        attempt = 0
        max_attempts = 1 + _MAX_RETRIES  # 1 initial + 2 retries

        while attempt < max_attempts:
            t0 = time.monotonic()
            rc, stdout, stderr = await _run_cli(cmd)
            duration_ms = int((time.monotonic() - t0) * 1000)

            if rc == 0:
                # Success — find output file(s)
                output_paths = self._collect_output_files(out_path, n, stdout)
                attachments = [
                    Attachment(
                        type="image",
                        url=str(p),
                        filename=p.name,
                        mime_type="image/png",
                        size_bytes=p.stat().st_size if p.exists() else 0,
                    )
                    for p in output_paths
                    if p.exists()
                ]

                if not attachments:
                    return SkillOutput(
                        status="error",
                        error_info="CLI returned success but no output files found.",
                        metadata={
                            "error_type": "api_error",
                            "retryable": False,
                            "recovery_action": {
                                "type": "report_error",
                                "reason": "no_output",
                                "message": "Image generation completed but no files were produced.",
                            },
                        },
                    )

                return SkillOutput(
                    status="success",
                    result={
                        "image_paths": [str(p) for p in output_paths],
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "n": n,
                        "duration_ms": duration_ms,
                    },
                    attachments=attachments,
                    metadata={
                        "tool": "generate_image",
                        "model": "gpt-image-2",
                        "cli_subcommand": "generate",
                    },
                )

            # Error path
            last_error = _classify_error(rc, stderr)
            if not last_error.get("retryable", False):
                break

            attempt += 1
            if attempt < max_attempts:
                backoff = _RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)

        # All retries exhausted or non-retryable error
        recovery = last_error.get("recovery_action", {})
        return SkillOutput(
            status="error",
            error_info=recovery.get("message", f"Image generation failed: {stderr[:200]}"),
            metadata={
                "tool": "generate_image",
                "error_type": last_error.get("error_type", "unknown"),
                "retryable": False,
                "retry_count": attempt,
                "recovery_action": recovery,
            },
        )

    def _collect_output_files(
        self,
        primary_path: Path,
        n: int,
        stdout: str,
    ) -> list[Path]:
        """Collect generated output files from the output directory."""
        # gpt_image_cli may save with different naming for n > 1
        # Try the primary path first, then parse stdout, then scan directory
        paths: list[Path] = []
        if primary_path.exists():
            paths.append(primary_path)

        # Try parsing stdout for "Saved to <path>" or similar patterns
        if len(paths) < n and stdout:
            saved_match = re.search(r"Saved to\s+(.+)", stdout)
            if saved_match:
                parsed = Path(saved_match.group(1).strip())
                if parsed.exists() and parsed not in paths:
                    paths.append(parsed)

        if len(paths) < n:
            # Scan output directory for recent PNG files
            parent = primary_path.parent
            if parent.exists():
                for f in sorted(parent.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True):
                    if f not in paths and f.stem.startswith("img_"):
                        paths.append(f)
                        if len(paths) >= n:
                            break

        return paths[:n]

    # ------------------------------------------------------------------
    # Image editing
    # ------------------------------------------------------------------

    async def _edit_image(
        self,
        *,
        image_url: str,
        prompt: str,
        mask_url: str | None,
        input_fidelity: str,
        size: str,
        quality: str,
        n: int,
    ) -> SkillOutput:
        """Edit an existing image with retry logic."""
        if not image_url.strip():
            return SkillOutput(
                status="error",
                error_info="image_url is required and must not be empty.",
                metadata={"error_type": "invalid_params", "retryable": False},
            )
        if not prompt.strip():
            return SkillOutput(
                status="error",
                error_info="prompt is required and must not be empty.",
                metadata={"error_type": "invalid_params", "retryable": False},
            )

        # Resolve source image (URL or local path)
        tmp_dir = self._output_dir / "tmp"
        source_path = await self._resolve_image_path(image_url, tmp_dir)
        if source_path is None:
            return SkillOutput(
                status="error",
                error_info=f"Could not resolve image: {image_url}. Check URL or local path.",
                metadata={
                    "error_type": "image_not_found",
                    "retryable": False,
                    "recovery_action": {
                        "type": "report_error",
                        "reason": "image_not_found",
                        "message": f"Image not found: {image_url}",
                    },
                },
            )

        # Resolve mask if provided
        mask_path_obj: Path | None = None
        if mask_url:
            mask_path_obj = await self._resolve_image_path(mask_url, tmp_dir)
            if mask_path_obj is None:
                return SkillOutput(
                    status="error",
                    error_info=f"Could not resolve mask image: {mask_url}.",
                    metadata={
                        "error_type": "image_not_found",
                        "retryable": False,
                        "recovery_action": {
                            "type": "report_error",
                            "reason": "mask_not_found",
                            "message": f"Mask image not found: {mask_url}",
                        },
                    },
                )

        # Generate output filename
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        out_path = self._output_dir / f"edit_{ts}.png"

        cmd = self._build_edit_command(
            prompt=prompt,
            image_path=str(source_path),
            mask_path=str(mask_path_obj) if mask_path_obj else None,
            input_fidelity=input_fidelity,
            size=size,
            quality=quality,
            n=n,
            out_path=str(out_path),
        )

        last_error: dict[str, Any] = {}
        attempt = 0
        max_attempts = 1 + _MAX_RETRIES

        while attempt < max_attempts:
            t0 = time.monotonic()
            rc, stdout, stderr = await _run_cli(cmd)
            duration_ms = int((time.monotonic() - t0) * 1000)

            if rc == 0:
                output_paths = self._collect_output_files(out_path, n, stdout)
                attachments = [
                    Attachment(
                        type="image",
                        url=str(p),
                        filename=p.name,
                        mime_type="image/png",
                        size_bytes=p.stat().st_size if p.exists() else 0,
                    )
                    for p in output_paths
                    if p.exists()
                ]

                if not attachments:
                    return SkillOutput(
                        status="error",
                        error_info="CLI returned success but no output files found.",
                        metadata={
                            "error_type": "api_error",
                            "retryable": False,
                            "recovery_action": {
                                "type": "report_error",
                                "reason": "no_output",
                                "message": "Image editing completed but no files were produced.",
                            },
                        },
                    )

                return SkillOutput(
                    status="success",
                    result={
                        "image_paths": [str(p) for p in output_paths],
                        "prompt": prompt,
                        "source_image": str(source_path),
                        "size": size,
                        "quality": quality,
                        "n": n,
                        "duration_ms": duration_ms,
                    },
                    attachments=attachments,
                    metadata={
                        "tool": "edit_image",
                        "model": "gpt-image-2",
                        "cli_subcommand": "edit",
                    },
                )

            last_error = _classify_error(rc, stderr)
            if not last_error.get("retryable", False):
                break

            attempt += 1
            if attempt < max_attempts:
                backoff = _RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)

        recovery = last_error.get("recovery_action", {})
        return SkillOutput(
            status="error",
            error_info=recovery.get("message", f"Image editing failed: {stderr[:200]}"),
            metadata={
                "tool": "edit_image",
                "error_type": last_error.get("error_type", "unknown"),
                "retryable": False,
                "retry_count": attempt,
                "recovery_action": recovery,
            },
        )


# ---------------------------------------------------------------------------
# Intent detection (Agent NLU)
# ---------------------------------------------------------------------------

# Trigger patterns for image generation intent
_GENERATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:帮我|请|麻烦)?\s*(?:画|绘|生成|创建|制作|出)\s*(?:一张|一幅|一个|几|些)?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:generate|create|make|draw|paint|produce)\s+(?:an?\s+)?(?:image|picture|photo|illustration|drawing)\s+(?:of\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:i\s+)?(?:want|need|like)\s+(?:an?\s+)?(?:image|picture|photo)\s+(?:of\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:画|绘|生成|创建)\s*(?:图片|图像|照片|插画)\s*[:：]?\s*(.+)", re.IGNORECASE),
]

# Trigger patterns for image editing intent
_EDIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:给|把|对)\s*(?:这张|这张|那张|这个)?\s*(?:图片|图像|照片)\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:edit|modify|transform|change|alter)\s+(?:this|the|that)\s+(?:image|picture|photo)\s*[:：]?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:加上|添加|去除|删除|替换|修改|美化|调整)\s*(.+)", re.IGNORECASE),
]

# Style keywords
_STYLE_KEYWORDS: dict[str, str] = {
    "水彩": "watercolor",
    "油画": "oil painting",
    "素描": "sketch",
    "动漫": "anime",
    "卡通": "cartoon",
    "写实": "photorealistic",
    "像素": "pixel art",
    "赛博朋克": "cyberpunk",
    "复古": "vintage",
    "极简": "minimalist",
}


def detect_image_generation_intent(text: str) -> dict[str, Any] | None:
    """Detect whether *text* contains an image generation or editing intent.

    Returns a dict with ``intent`` (``"generate_image"`` or ``"edit_image"``),
    ``prompt`` (the extracted description), and optional ``style``, or ``None``
    if no intent is detected.
    """
    text = text.strip()
    if not text or len(text) < 2:
        return None

    # Check edit patterns first (more specific)
    for pattern in _EDIT_PATTERNS:
        match = pattern.search(text)
        if match:
            prompt = match.group(1).strip()
            if prompt:
                result: dict[str, Any] = {"intent": "edit_image", "prompt": prompt}
                # Extract style if present
                for cn_style, en_style in _STYLE_KEYWORDS.items():
                    if cn_style in text:
                        result["style"] = en_style
                        break
                return result

    # Check generate patterns
    for pattern in _GENERATE_PATTERNS:
        match = pattern.search(text)
        if match:
            prompt = match.group(1).strip()
            if prompt:
                result = {"intent": "generate_image", "prompt": prompt}
                # Extract style if present
                for cn_style, en_style in _STYLE_KEYWORDS.items():
                    if cn_style in text:
                        result["style"] = en_style
                        break
                return result

    return None
