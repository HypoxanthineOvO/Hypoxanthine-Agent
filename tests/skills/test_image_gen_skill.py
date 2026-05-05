"""Tests for ImageGenSkill — text-to-image core (C2 M1)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hypo_agent.skills.image_gen_skill import ImageGenSkill


class TestGenerateImageToolsSchema:
    """Unit tests for tool schema."""

    def test_tools_returns_generate_image_schema(self) -> None:
        skill = ImageGenSkill()
        tools = skill.tools
        assert len(tools) == 2  # generate_image + edit_image
        tool_names = {t["function"]["name"] for t in tools}
        assert "generate_image" in tool_names
        assert "edit_image" in tool_names

    def test_generate_image_schema_has_required_params(self) -> None:
        skill = ImageGenSkill()
        schema = skill.tools[0]["function"]["parameters"]
        assert "prompt" in schema["properties"]
        assert "prompt" in schema["required"]

    def test_generate_image_schema_has_optional_params(self) -> None:
        skill = ImageGenSkill()
        props = skill.tools[0]["function"]["parameters"]["properties"]
        assert "size" in props
        assert "quality" in props
        assert "n" in props
        assert "style" in props
        assert "negative" in props

    def test_generate_image_schema_size_enum(self) -> None:
        skill = ImageGenSkill()
        size = skill.tools[0]["function"]["parameters"]["properties"]["size"]
        assert "1024x1024" in size["enum"]
        assert "1536x1024" in size["enum"]
        assert "1024x1536" in size["enum"]

    def test_generate_image_schema_quality_enum(self) -> None:
        skill = ImageGenSkill()
        quality = skill.tools[0]["function"]["parameters"]["properties"]["quality"]
        assert "low" in quality["enum"]
        assert "medium" in quality["enum"]
        assert "high" in quality["enum"]

    def test_generate_image_schema_n_bounds(self) -> None:
        skill = ImageGenSkill()
        n = skill.tools[0]["function"]["parameters"]["properties"]["n"]
        assert n["minimum"] == 1
        assert n["maximum"] == 4


class TestCommandBuilding:
    """Unit tests for CLI command construction."""

    def test_build_generate_command_basic(self) -> None:
        skill = ImageGenSkill()
        cmd = skill._build_generate_command(
            prompt="a cat",
            size="1024x1024",
            quality="medium",
            n=1,
            out_path="/tmp/test.png",
        )
        assert "gpt_image_cli" in cmd
        assert "generate" in cmd
        assert "--model" in cmd
        assert "gpt-image-2" in cmd
        assert "--prompt" in cmd
        assert "a cat" in cmd
        assert "--size" in cmd
        assert "1024x1024" in cmd
        assert "--quality" in cmd
        assert "medium" in cmd
        assert "--n" in cmd
        assert "1" in cmd
        assert "--out" in cmd
        assert "/tmp/test.png" in cmd

    def test_build_generate_command_with_style(self) -> None:
        skill = ImageGenSkill()
        cmd = skill._build_generate_command(
            prompt="a cat",
            size="1024x1024",
            quality="high",
            n=2,
            style="watercolor",
            negative="blurry",
            out_path="/tmp/test.png",
        )
        assert "--style" in cmd
        assert "watercolor" in cmd
        assert "--negative" in cmd
        assert "blurry" in cmd
        assert "--n" in cmd
        assert "2" in cmd

    def test_build_generate_command_default_params(self) -> None:
        skill = ImageGenSkill()
        cmd = skill._build_generate_command(
            prompt="a dog",
            out_path="/tmp/test.png",
        )
        assert "--model" in cmd
        assert "gpt-image-2" in cmd
        assert "--prompt" in cmd
        assert "a dog" in cmd


class TestGenerateImageSuccess:
    """Integration tests for successful image generation."""

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_generate_image_returns_success_with_attachment(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        async def mock_cli(cmd, **kwargs):
            # Extract --out path from command and create the file
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"fake_png_data")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {"prompt": "a cat"}))

        assert result.status == "success"
        assert len(result.attachments) == 1
        assert result.attachments[0].type == "image"
        assert result.attachments[0].mime_type == "image/png"

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_generate_image_passes_all_params(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        async def mock_cli(cmd, **kwargs):
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"fake_png_data")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {
            "prompt": "a dog",
            "size": "1536x1024",
            "quality": "high",
            "n": 2,
            "style": "anime",
            "negative": "blurry",
        }))

        assert result.status == "success"
        # Verify CLI was called with correct params
        call_args = mock_run_cli.call_args
        cmd = call_args[0][0]
        assert "--prompt" in cmd
        assert "a dog" in cmd
        assert "--size" in cmd
        assert "1536x1024" in cmd
        assert "--style" in cmd
        assert "anime" in cmd
        assert "--negative" in cmd
        assert "blurry" in cmd


class TestGenerateImageFailure:
    """Integration tests for failure scenarios."""

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_cli_not_available(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        mock_run_cli.return_value = (-1, "", "gpt_image_cli not found")

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {"prompt": "a cat"}))

        assert result.status == "error"
        assert "setup" in result.error_info.lower() or "not found" in result.error_info.lower()
        assert result.metadata.get("error_type") == "cli_not_found"
        assert result.metadata.get("retryable") is False

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_content_policy_rejection(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        mock_run_cli.return_value = (1, "", "Error: content policy violation")

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {"prompt": "inappropriate"}))

        assert result.status == "error"
        assert "content" in result.error_info.lower()
        assert result.metadata.get("error_type") == "content_policy"
        assert result.metadata.get("retryable") is False

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_network_timeout_retries(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        call_count = 0

        async def mock_cli(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (1, "", "Connection timed out")
            # On third call, create the output file and succeed
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"fake_png")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {"prompt": "a cat"}))

        # After 2 timeouts + 1 success, should be success
        assert result.status == "success"
        assert call_count == 3

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_invalid_params_immediate_failure(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        mock_run_cli.return_value = (2, "", "error: argument --size: invalid choice")

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("generate_image", {
            "prompt": "a cat",
            "size": "invalid_size",
        }))

        assert result.status == "error"
        assert result.metadata.get("error_type") == "invalid_params"
        assert result.metadata.get("retryable") is False


class TestSkillRegistration:
    """Test that ImageGenSkill can be registered with SkillManager."""

    def test_skill_has_name(self) -> None:
        skill = ImageGenSkill()
        assert skill.name == "image_gen"

    def test_skill_has_description(self) -> None:
        skill = ImageGenSkill()
        assert len(skill.description) > 0

    def test_skill_registerable(self, tmp_path: Path) -> None:
        from hypo_agent.core.skill_manager import SkillManager

        manager = SkillManager()
        manager.register(ImageGenSkill(output_dir=tmp_path))

        tool_names = {tool["function"]["name"] for tool in manager.get_tools_schema()}
        assert "generate_image" in tool_names

    def test_skill_has_edit_image_tool(self, tmp_path: Path) -> None:
        from hypo_agent.core.skill_manager import SkillManager

        manager = SkillManager()
        manager.register(ImageGenSkill(output_dir=tmp_path))

        tool_names = {tool["function"]["name"] for tool in manager.get_tools_schema()}
        assert "edit_image" in tool_names


class TestEditImageTool:
    """Tests for the edit_image tool (image-to-image editing)."""

    def test_edit_image_schema_has_required_params(self) -> None:
        skill = ImageGenSkill()
        tools = {t["function"]["name"]: t for t in skill.tools}
        assert "edit_image" in tools
        schema = tools["edit_image"]["function"]["parameters"]
        assert "image_url" in schema["required"]
        assert "prompt" in schema["required"]

    def test_edit_image_schema_has_optional_params(self) -> None:
        skill = ImageGenSkill()
        tools = {t["function"]["name"]: t for t in skill.tools}
        props = tools["edit_image"]["function"]["parameters"]["properties"]
        assert "mask_url" in props
        assert "input_fidelity" in props
        assert "size" in props
        assert "quality" in props
        assert "n" in props

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_edit_image_with_local_path(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        # Create a source image
        source_img = tmp_path / "source.png"
        source_img.write_bytes(b"fake_source_png")

        async def mock_cli(cmd, **kwargs):
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"edited_png")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("edit_image", {
            "image_url": str(source_img),
            "prompt": "add sunglasses",
        }))

        assert result.status == "success"
        assert len(result.attachments) == 1
        assert result.attachments[0].type == "image"
        # Verify CLI was called with --image
        cmd = mock_run_cli.call_args[0][0]
        assert "--image" in cmd
        assert str(source_img) in cmd

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_edit_image_with_mask(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        source_img = tmp_path / "source.png"
        source_img.write_bytes(b"fake_source_png")
        mask_img = tmp_path / "mask.png"
        mask_img.write_bytes(b"fake_mask_png")

        async def mock_cli(cmd, **kwargs):
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"edited_png")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("edit_image", {
            "image_url": str(source_img),
            "prompt": "remove the background",
            "mask_url": str(mask_img),
        }))

        assert result.status == "success"
        cmd = mock_run_cli.call_args[0][0]
        assert "--mask" in cmd
        assert str(mask_img) in cmd

    def test_edit_image_missing_image_url(self) -> None:
        skill = ImageGenSkill()
        result = asyncio.run(skill.execute("edit_image", {
            "prompt": "add sunglasses",
        }))

        assert result.status == "error"
        assert "image_url" in result.error_info.lower()

    def test_edit_image_nonexistent_local_path(self) -> None:
        skill = ImageGenSkill()
        result = asyncio.run(skill.execute("edit_image", {
            "image_url": "/nonexistent/path/image.png",
            "prompt": "add sunglasses",
        }))

        assert result.status == "error"
        assert "resolve" in result.error_info.lower() or "not found" in result.error_info.lower()


class TestUrlDownload:
    """Tests for URL reference image downloading."""

    @patch("hypo_agent.skills.image_gen_skill._download_url", new_callable=AsyncMock)
    def test_download_url_saves_to_temp(self, mock_download: AsyncMock, tmp_path: Path) -> None:
        downloaded_path = tmp_path / "downloaded.png"
        downloaded_path.write_bytes(b"downloaded_png")
        mock_download.return_value = downloaded_path

        async def mock_cli(cmd, **kwargs):
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"edited_png")
            return (0, "Saved to output", "")

        with patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock, side_effect=mock_cli):
            skill = ImageGenSkill(output_dir=tmp_path)
            result = asyncio.run(skill.execute("edit_image", {
                "image_url": "https://example.com/cat.png",
                "prompt": "add hat",
            }))

            assert result.status == "success"
            mock_download.assert_called_once()

    @patch("hypo_agent.skills.image_gen_skill._download_url", new_callable=AsyncMock)
    def test_download_url_failure(self, mock_download: AsyncMock, tmp_path: Path) -> None:
        mock_download.return_value = None  # Download failed

        skill = ImageGenSkill(output_dir=tmp_path)
        result = asyncio.run(skill.execute("edit_image", {
            "image_url": "https://example.com/nonexistent.png",
            "prompt": "add hat",
        }))

        assert result.status == "error"
        assert "download" in result.error_info.lower() or "url" in result.error_info.lower()

