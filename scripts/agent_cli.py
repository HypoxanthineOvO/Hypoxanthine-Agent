#!/usr/bin/env python3
"""CLI client for interacting with a running Hypo-Agent instance."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

try:
    import websockets
except ImportError:  # pragma: no cover - runtime dependency hint
    print("Missing dependency: websockets. Install with: pip install websockets", file=sys.stderr)
    raise


def _load_token(config_path: Path = Path("config/security.yaml")) -> str:
    if not config_path.exists():
        raise FileNotFoundError(f"Missing security config: {config_path}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Invalid security.yaml format")

    token = payload.get("auth_token")
    if isinstance(token, str) and token.strip():
        return token.strip()

    nested = payload.get("security")
    if isinstance(nested, dict):
        nested_token = nested.get("auth_token")
        if isinstance(nested_token, str) and nested_token.strip():
            return nested_token.strip()

    raise ValueError("auth_token not found in config/security.yaml")


def _ws_url(port: int, token: str) -> str:
    return f"ws://localhost:{port}/ws?token={token}"


def _format_message(prefix: str, payload: dict[str, Any]) -> str:
    msg_type = str(payload.get("type") or "message")
    sender = str(payload.get("sender") or "?")
    tag = str(payload.get("message_tag") or "")
    text = str(payload.get("text") or "")

    if msg_type == "assistant_chunk":
        return f"{prefix}[assistant_chunk] {text[:200]}"
    if msg_type == "assistant_done":
        return f"{prefix}[assistant_done]"
    if msg_type == "tool_call_start":
        return f"{prefix}[tool_start] {payload.get('tool_name', '')}"
    if msg_type == "tool_call_result":
        status = payload.get("status", "")
        return f"{prefix}[tool_result:{status}] {payload.get('tool_name', '')}"

    marker = f"[{sender}]"
    if tag:
        marker += f"[{tag}]"
    return f"{prefix}{marker} {text[:200]}"


async def cmd_send(text: str, *, port: int, session_id: str, wait: int) -> int:
    token = _load_token()
    uri = _ws_url(port, token)
    started = time.time()

    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"text": text, "sender": "user", "session_id": session_id}))
        print(f"[SENT] {text}")

        saw_done = False
        while time.time() - started < wait:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                if saw_done:
                    break
                continue

            payload = json.loads(raw)
            elapsed = int(time.time() - started)
            print(_format_message(f"[{elapsed}s]", payload))

            if str(payload.get("type") or "") == "assistant_done":
                saw_done = True

        print(f"[END] finished in {int(time.time() - started)}s")
    return 0


async def cmd_listen(duration: int, *, port: int, session_id: str) -> int:
    token = _load_token()
    uri = _ws_url(port, token)
    started = time.time()
    received = 0

    async with websockets.connect(uri) as ws:
        print(f"[LISTEN] waiting {duration}s ...")
        while time.time() - started < duration:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue

            payload = json.loads(raw)
            if str(payload.get("session_id") or "") != session_id:
                continue
            received += 1
            elapsed = int(time.time() - started)
            print(_format_message(f"[{elapsed}s]", payload))

    print(f"[END] received={received}")
    return 0


def cmd_check_db(query: str, db_path: Path = Path("memory/hypo.db")) -> int:
    if not db_path.exists():
        print(f"[ERROR] DB not found: {db_path}", file=sys.stderr)
        return 1

    result = subprocess.run(
        ["sqlite3", "-header", "-column", str(db_path), query],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def _query_db_value(query: str, db_path: Path = Path("memory/hypo.db")) -> str:
    result = subprocess.run(
        ["sqlite3", str(db_path), query],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _query_db_rows(query: str, db_path: Path = Path("memory/hypo.db")) -> list[list[str]]:
    result = subprocess.run(
        ["sqlite3", str(db_path), query],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    rows: list[list[str]] = []
    for line in lines:
        rows.append([cell.strip() for cell in line.split("|")])
    return rows


async def _smoke_send(ws: Any, text: str, session_id: str) -> None:
    await ws.send(json.dumps({"text": text, "sender": "user", "session_id": session_id}))


async def cmd_smoke(*, port: int, session_id: str) -> int:
    token = _load_token()
    uri = _ws_url(port, token)

    print("=" * 60)
    print("SMOKE TEST: Hypo-Agent m8_smoke flow")
    print("=" * 60)

    unique_title = f"m8_smoke_{int(time.time())}"
    trigger_at = (datetime.now() + timedelta(minutes=1)).replace(microsecond=0).isoformat()

    subprocess.run(
        [
            "sqlite3",
            "memory/hypo.db",
            "DELETE FROM reminders WHERE title LIKE 'm8_smoke_%' OR title LIKE '%m8_smoke%';",
        ],
        check=False,
    )
    baseline_id_raw = _query_db_value("SELECT COALESCE(MAX(id), 0) FROM reminders;")
    baseline_id = int(baseline_id_raw) if baseline_id_raw.isdigit() else 0

    created = False
    listed = False
    pushed = False
    created_ids: list[int] = []
    created_titles: list[str] = []

    async with websockets.connect(uri) as ws:
        print("[STEP 1] create reminder")
        create_prompt = (
            "请务必调用 create_reminder 工具创建提醒，不要只给文字答复。"
            f"参数：title={unique_title}，schedule_type=once，schedule_value={trigger_at}，channel=all。"
        )
        await _smoke_send(ws, create_prompt, session_id)

        step_started = time.time()
        while time.time() - step_started < 30:
            try:
                payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                continue
            if str(payload.get("session_id") or "") != session_id:
                continue
            text = str(payload.get("text") or "")
            payload_type = str(payload.get("type") or "")
            if payload_type in {"assistant_chunk", "tool_call_result"} and text:
                print(f"  {_format_message('[event]', payload)}")
            if payload_type == "assistant_done":
                break

        created_rows = _query_db_rows(
            f"SELECT id, title, status FROM reminders WHERE id > {baseline_id} ORDER BY id DESC;"
        )
        created_ids = [
            int(row[0])
            for row in created_rows
            if row and row[0].isdigit()
        ]
        created_titles = [row[1] for row in created_rows if len(row) >= 2 and row[1]]
        created = len(created_ids) > 0
        if not created:
            retry_prompt = (
                "上一步没有创建成功。现在请直接调用 create_reminder 工具，"
                f"title={unique_title}，schedule_type=once，schedule_value={trigger_at}。"
            )
            await _smoke_send(ws, retry_prompt, session_id)
            retry_started = time.time()
            while time.time() - retry_started < 20:
                try:
                    payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                except asyncio.TimeoutError:
                    continue
                if str(payload.get("session_id") or "") != session_id:
                    continue
                if str(payload.get("type") or "") == "assistant_done":
                    break
            created_rows = _query_db_rows(
                f"SELECT id, title, status FROM reminders WHERE id > {baseline_id} ORDER BY id DESC;"
            )
            created_ids = [
                int(row[0])
                for row in created_rows
                if row and row[0].isdigit()
            ]
            created_titles = [row[1] for row in created_rows if len(row) >= 2 and row[1]]
            created = len(created_ids) > 0

        print("[STEP 2] list reminders")
        await _smoke_send(ws, "/reminders", session_id)
        list_chunks: list[str] = []
        step_started = time.time()
        while time.time() - step_started < 20:
            try:
                payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                continue
            if str(payload.get("session_id") or "") != session_id:
                continue
            payload_type = str(payload.get("type") or "")
            if payload_type == "assistant_chunk":
                list_chunks.append(str(payload.get("text") or ""))
                continue
            if payload_type == "assistant_done":
                break

        list_text = "".join(list_chunks)
        listed = any(f"#{reminder_id}" in list_text for reminder_id in created_ids)
        if listed:
            print("  reminder appears in /reminders output")

        print("[STEP 3] wait for proactive reminder push (<=120s)")
        step_started = time.time()
        while time.time() - step_started < 120:
            try:
                payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                continue
            if str(payload.get("session_id") or "") != session_id:
                continue
            tag = str(payload.get("message_tag") or "")
            text = str(payload.get("text") or "")
            if tag == "reminder":
                if created_titles and not any(title in text for title in created_titles):
                    continue
                pushed = True
                print(f"  reminder push received at {int(time.time() - step_started)}s")
                break

    ids_sql = ",".join(str(item) for item in created_ids) if created_ids else "0"
    db_rows: list[list[str]] = []
    db_ok = False
    for _ in range(20):
        db_rows = _query_db_rows(
            f"SELECT id, title, status FROM reminders WHERE id IN ({ids_sql}) ORDER BY id DESC;"
        )
        db_ok = any(len(row) >= 3 and row[2] == "completed" for row in db_rows)
        if db_ok:
            break
        await asyncio.sleep(0.5)
    db_text = "\\n".join("|".join(row) for row in db_rows).strip()
    print("[STEP 4] db status")
    print(db_text if db_text else "(no rows)")

    checks = [
        ("create reminder", created),
        ("list reminder", listed),
        ("receive push", pushed),
        ("db status completed", db_ok),
    ]
    print("=" * 60)
    all_ok = True
    for name, ok in checks:
        print(f"{'✅' if ok else '❌'} {name}")
        all_ok = all_ok and ok
    print("=" * 60)
    if all_ok:
        print("ALL SMOKE TESTS PASSED")
        return 0
    print("SMOKE TEST FAILED")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Hypo-Agent CLI")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--session-id", default="main")

    sub = parser.add_subparsers(dest="command", required=True)

    send_parser = sub.add_parser("send", help="send one message")
    send_parser.add_argument("text")
    send_parser.add_argument("--wait", type=int, default=30)

    listen_parser = sub.add_parser("listen", help="listen proactive messages")
    listen_parser.add_argument("duration", type=int)

    db_parser = sub.add_parser("check-db", help="run sqlite query")
    db_parser.add_argument("query")

    sub.add_parser("smoke", help="run m8_smoke test")

    args = parser.parse_args()

    if args.command == "send":
        raise SystemExit(
            asyncio.run(
                cmd_send(
                    args.text,
                    port=args.port,
                    session_id=args.session_id,
                    wait=args.wait,
                )
            )
        )
    if args.command == "listen":
        raise SystemExit(
            asyncio.run(
                cmd_listen(
                    args.duration,
                    port=args.port,
                    session_id=args.session_id,
                )
            )
        )
    if args.command == "check-db":
        raise SystemExit(cmd_check_db(args.query))
    if args.command == "smoke":
        raise SystemExit(asyncio.run(cmd_smoke(port=args.port, session_id=args.session_id)))


if __name__ == "__main__":
    main()
