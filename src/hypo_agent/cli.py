from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from hypo_agent.core.outbound_send import OutboundSendResult


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hypo-Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="send a message to HYX")
    send.add_argument("--token", default=None)
    send.add_argument("--text", default="")
    send.add_argument("--image", dest="images", action="append", default=[])
    send.add_argument("--file", dest="files", action="append", default=[])
    send.add_argument("--json", dest="json_path", default="")
    send.add_argument("--stdin", action="store_true")
    send.add_argument("--channel", dest="channels", action="append", default=[])
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--output", choices=("pretty", "json"), default="pretty")
    send.add_argument("--host", default="127.0.0.1")
    send.add_argument("--port", type=int, default=int(os.getenv("HYPO_PORT", "8765")))
    return parser.parse_args(argv)


def build_send_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if getattr(args, "json_path", ""):
        payload.update(json.loads(Path(args.json_path).read_text(encoding="utf-8")))
    if getattr(args, "stdin", False):
        payload.update(json.loads(sys.stdin.read()))

    if getattr(args, "text", ""):
        payload["text"] = args.text
    if getattr(args, "images", []):
        payload["images"] = list(args.images)
    if getattr(args, "files", []):
        payload["files"] = list(args.files)
    if getattr(args, "channels", []):
        payload["channels"] = list(args.channels)
    if getattr(args, "dry_run", False):
        payload["dry_run"] = True
    return payload


def format_send_result(result: OutboundSendResult | dict[str, Any], *, output: str, token: str | None) -> str:
    payload = result.to_payload() if isinstance(result, OutboundSendResult) else dict(result)
    payload["token"] = "[REDACTED]" if token else None
    if output == "json":
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    lines = ["Hypo-Agent send result:"]
    lines.append(f"- success: {payload.get('success')}")
    lines.append(f"- dry_run: {payload.get('dry_run')}")
    for channel, status in dict(payload.get("channel_results") or {}).items():
        icon = "planned" if status.get("planned") else ("ok" if status.get("success") else "failed")
        suffix = f" ({status.get('error')})" if status.get("error") else ""
        lines.append(f"- {channel}: {icon}{suffix}")
    return "\n".join(lines)


def run_send(args: argparse.Namespace) -> int:
    token = str(args.token or os.getenv("HYPO_AGENT_TOKEN") or "").strip()
    payload = build_send_payload(args)
    url = f"http://{args.host}:{args.port}/api/outbound/send"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"hypo-agent send failed: {exc}", file=sys.stderr)
        return 1
    print(format_send_result(result, output=args.output, token=token))
    return 0 if bool(result.get("success")) or bool(result.get("dry_run")) else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_cli_args(argv)
    if args.command == "send":
        return run_send(args)
    return 2
