from __future__ import annotations

import json
from pathlib import Path


def test_parse_send_args_supports_text_files_channels_and_dry_run(tmp_path: Path) -> None:
    image = tmp_path / "cat.png"
    image.write_bytes(b"png")
    report = tmp_path / "report.txt"
    report.write_text("hello", encoding="utf-8")

    from hypo_agent.cli import parse_cli_args

    args = parse_cli_args(
        [
            "send",
            "--token",
            "secret-token",
            "--text",
            "hello",
            "--image",
            str(image),
            "--file",
            str(report),
            "--channel",
            "qq",
            "--channel",
            "weixin",
            "--dry-run",
            "--output",
            "json",
        ]
    )

    assert args.command == "send"
    assert args.text == "hello"
    assert args.images == [str(image)]
    assert args.files == [str(report)]
    assert args.channels == ["qq", "weixin"]
    assert args.dry_run is True
    assert args.output == "json"


def test_send_payload_can_be_loaded_from_json_file(tmp_path: Path) -> None:
    payload = {
        "text": "hello",
        "images": ["a.png"],
        "files": ["report.txt"],
        "channels": ["feishu"],
        "dry_run": True,
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    from hypo_agent.cli import build_send_payload, parse_cli_args

    args = parse_cli_args(["send", "--json", str(payload_path), "--token", "token"])

    assert build_send_payload(args) == payload


def test_json_output_redacts_token() -> None:
    from hypo_agent.cli import format_send_result
    from hypo_agent.core.outbound_send import OutboundSendResult

    result = OutboundSendResult(
        success=True,
        dry_run=True,
        target_channels=["qq"],
        channel_results={"qq": {"success": None, "planned": True, "error": None}},
        attachments=[],
        error=None,
    )

    rendered = format_send_result(result, output="json", token="super-secret")

    assert "super-secret" not in rendered
    assert json.loads(rendered)["token"] == "[REDACTED]"
