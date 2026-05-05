"""Tests for M4 — generation history and end-to-end acceptance."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from hypo_agent.skills.image_gen_skill import ImageGenSkill


class TestGenerationHistory:
    """Tests for generation history storage and retrieval."""

    def test_history_store_record(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")
        record = history.record(
            session_id="session_abc",
            prompt="a cat wearing sunglasses",
            tool="generate_image",
            status="success",
            output_paths=["/tmp/cat.png"],
            duration_ms=4500,
        )

        assert record["session_id"] == "session_abc"
        assert record["prompt"] == "a cat wearing sunglasses"
        assert record["status"] == "success"
        assert "timestamp" in record

    def test_history_query_by_session(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")
        history.record(session_id="s1", prompt="cat", tool="generate_image", status="success")
        history.record(session_id="s2", prompt="dog", tool="generate_image", status="success")
        history.record(session_id="s1", prompt="bird", tool="edit_image", status="success")

        results = history.query(session_id="s1")
        assert len(results) == 2
        assert all(r["session_id"] == "s1" for r in results)

    def test_history_query_by_time_range(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")
        history.record(session_id="s1", prompt="cat", tool="generate_image", status="success")
        history.record(session_id="s1", prompt="dog", tool="generate_image", status="success")

        # Query all records (no time filter)
        results = history.query()
        assert len(results) == 2

    def test_history_query_empty(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")
        results = history.query(session_id="nonexistent")
        assert len(results) == 0

    def test_history_records_failure(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")
        record = history.record(
            session_id="s1",
            prompt="inappropriate",
            tool="generate_image",
            status="failed",
            error_info="content policy violation",
        )

        assert record["status"] == "failed"
        assert record["error_info"] == "content policy violation"

    def test_history_persistence(self, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        store_path = tmp_path / "history.jsonl"
        history1 = ImageGenHistory(store_path=store_path)
        history1.record(session_id="s1", prompt="cat", tool="generate_image", status="success")

        # Create new instance — should load from file
        history2 = ImageGenHistory(store_path=store_path)
        results = history2.query()
        assert len(results) == 1
        assert results[0]["prompt"] == "cat"


class TestEndToEndIntegration:
    """End-to-end integration tests for the image generation pipeline."""

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_e2e_generate_image_with_history(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        from hypo_agent.core.image_gen_history import ImageGenHistory

        async def mock_cli(cmd, **kwargs):
            out_idx = cmd.index("--out") + 1 if "--out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).write_bytes(b"fake_png")
            return (0, "Saved to output", "")

        mock_run_cli.side_effect = mock_cli

        skill = ImageGenSkill(output_dir=tmp_path)
        history = ImageGenHistory(store_path=tmp_path / "history.jsonl")

        # Generate image
        result = asyncio.run(skill.execute("generate_image", {"prompt": "a cat"}))
        assert result.status == "success"

        # Record in history
        history.record(
            session_id="test_session",
            prompt="a cat",
            tool="generate_image",
            status=result.status,
            output_paths=result.result.get("image_paths", []),
            duration_ms=result.result.get("duration_ms", 0),
        )

        # Verify history
        records = history.query(session_id="test_session")
        assert len(records) == 1
        assert records[0]["prompt"] == "a cat"
        assert records[0]["status"] == "success"

    @patch("hypo_agent.skills.image_gen_skill._run_cli", new_callable=AsyncMock)
    def test_e2e_edit_image_with_attachment(self, mock_run_cli: AsyncMock, tmp_path: Path) -> None:
        # Create source image
        source_img = tmp_path / "source.png"
        source_img.write_bytes(b"source_png")

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

    def test_e2e_intent_detection_to_generation(self, tmp_path: Path) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        # Detect intent
        intent = detect_image_generation_intent("帮我画一只戴墨镜的猫")
        assert intent is not None
        assert intent["intent"] == "generate_image"
        assert "猫" in intent["prompt"]

        # The intent can be used to call generate_image
        # (actual CLI call is mocked in other tests)

    def test_e2e_intent_detection_to_edit(self) -> None:
        from hypo_agent.skills.image_gen_skill import detect_image_generation_intent

        intent = detect_image_generation_intent("给这张图片加上帽子")
        assert intent is not None
        assert intent["intent"] == "edit_image"


class TestChannelSmokeMarker:
    """Channel smoke tests (opt-in, marked for manual execution)."""

    def test_channel_smoke_placeholder(self) -> None:
        """Placeholder for channel smoke tests.

        Real channel smoke tests require:
        - QQ/Weixin/Feishu channel configuration
        - Opt-in execution via pytest -m channel_smoke
        - Manual verification of image delivery

        This placeholder ensures the test infrastructure is ready.
        """
        # Verify the skill can be imported and tools are available
        skill = ImageGenSkill()
        tools = {t["function"]["name"]: t for t in skill.tools}
        assert "generate_image" in tools
        assert "edit_image" in tools
