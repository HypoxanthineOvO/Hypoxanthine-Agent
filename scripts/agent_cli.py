#!/usr/bin/env python3
"""CLI client for interacting with a running Hypo-Agent instance."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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


class SmokeStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class SmokeCaseResult:
    name: str
    status: SmokeStatus
    detail: str = ""


class SmokeSession:
    def __init__(self, ws: Any, session_id: str) -> None:
        self._ws = ws
        self._session_id = session_id
        self.proactive_payloads: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        await _smoke_send(self._ws, text, self._session_id)

    async def recv_next(self, timeout: float = 5) -> dict[str, Any] | None:
        end_at = time.time() + timeout
        while time.time() < end_at:
            wait_seconds = max(0.1, min(5.0, end_at - time.time()))
            try:
                payload = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=wait_seconds))
            except asyncio.TimeoutError:
                continue

            if str(payload.get("session_id") or "") != self._session_id:
                continue

            payload_type = str(payload.get("type") or "")
            tag = str(payload.get("message_tag") or "")
            if tag or payload_type == "message":
                self.proactive_payloads.append(payload)
            return payload

        return None

    async def wait_for_assistant_done(self, timeout: int = 30) -> tuple[bool, list[str]]:
        chunks: list[str] = []
        end_at = time.time() + timeout
        while time.time() < end_at:
            payload = await self.recv_next(timeout=min(5, max(1, int(end_at - time.time()))))
            if payload is None:
                continue

            payload_type = str(payload.get("type") or "")
            if payload_type == "assistant_chunk":
                chunks.append(str(payload.get("text") or ""))
                continue
            if payload_type == "assistant_done":
                return True, chunks
        return False, chunks

    async def wait_for_tag(self, tag: str, timeout: int) -> dict[str, Any] | None:
        end_at = time.time() + timeout
        while time.time() < end_at:
            payload = await self.recv_next(timeout=min(5, max(1, int(end_at - time.time()))))
            if payload is None:
                continue
            if str(payload.get("message_tag") or "") == tag:
                return payload
        return None


def _load_tasks_config(config_path: Path = Path("config/tasks.yaml")) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if isinstance(payload, dict):
        return payload
    return {}


def _extract_interval_minutes(tasks_payload: dict[str, Any], key: str) -> tuple[bool, int | None]:
    section = tasks_payload.get(key)
    if not isinstance(section, dict):
        return False, None
    enabled = bool(section.get("enabled"))
    interval = section.get("interval_minutes")
    if isinstance(interval, int):
        return enabled, interval
    try:
        if interval is not None:
            return enabled, int(interval)
    except (TypeError, ValueError):
        return enabled, None
    return enabled, None


def _print_case_result(result: SmokeCaseResult) -> None:
    icon = {
        SmokeStatus.PASS: "✅",
        SmokeStatus.FAIL: "❌",
        SmokeStatus.SKIP: "⏭️",
    }[result.status]
    suffix = f" - {result.detail}" if result.detail else ""
    print(f"{icon} {result.name}: {result.status.value}{suffix}")


async def _case_send_regression(smoke: SmokeSession, text: str, timeout: int = 30) -> SmokeCaseResult:
    await smoke.send(text)
    done, chunks = await smoke.wait_for_assistant_done(timeout=timeout)
    if not done:
        return SmokeCaseResult(
            name=f'send "{text}" regression',
            status=SmokeStatus.FAIL,
            detail="assistant_done timeout",
        )
    if not chunks:
        return SmokeCaseResult(
            name=f'send "{text}" regression',
            status=SmokeStatus.FAIL,
            detail="no assistant chunks",
        )
    return SmokeCaseResult(
        name=f'send "{text}" regression',
        status=SmokeStatus.PASS,
        detail=f"chunks={len(chunks)}",
    )


async def _case_reminder_push_regression(smoke: SmokeSession) -> SmokeCaseResult:
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
    create_prompt = (
        "请务必调用 create_reminder 工具创建提醒，不要只给文字答复。"
        f"参数：title={unique_title}，schedule_type=once，schedule_value={trigger_at}，channel=all。"
    )
    await smoke.send(create_prompt)
    await smoke.wait_for_assistant_done(timeout=45)

    created_rows = _query_db_rows(
        f"SELECT id, title FROM reminders WHERE id > {baseline_id} ORDER BY id DESC;"
    )
    created_ids = [int(row[0]) for row in created_rows if row and row[0].isdigit()]
    created_titles = [row[1] for row in created_rows if len(row) >= 2 and row[1]]
    if not created_ids:
        retry_prompt = (
            "上一步未创建成功。请直接调用 create_reminder 工具，"
            f"title={unique_title}，schedule_type=once，schedule_value={trigger_at}。"
        )
        await smoke.send(retry_prompt)
        await smoke.wait_for_assistant_done(timeout=30)
        created_rows = _query_db_rows(
            f"SELECT id, title FROM reminders WHERE id > {baseline_id} ORDER BY id DESC;"
        )
        created_ids = [int(row[0]) for row in created_rows if row and row[0].isdigit()]
        created_titles = [row[1] for row in created_rows if len(row) >= 2 and row[1]]
    if not created_ids:
        return SmokeCaseResult("reminder create regression", SmokeStatus.FAIL, "db has no new reminder row")

    await smoke.send("/reminders")
    done, list_chunks = await smoke.wait_for_assistant_done(timeout=20)
    if not done:
        return SmokeCaseResult("send \"/reminders\" regression", SmokeStatus.FAIL, "assistant_done timeout")
    if not list_chunks:
        return SmokeCaseResult("send \"/reminders\" regression", SmokeStatus.FAIL, "no assistant chunks")

    list_text = "".join(list_chunks)
    if (unique_title not in list_text) and (not any(f"#{rid}" in list_text for rid in created_ids)):
        return SmokeCaseResult("send \"/reminders\" regression", SmokeStatus.FAIL, "created reminder not listed")

    reminder_payload = await smoke.wait_for_tag("reminder", timeout=120)
    if reminder_payload is None:
        return SmokeCaseResult("reminder proactive push regression", SmokeStatus.FAIL, "no reminder push")

    reminder_text = str(reminder_payload.get("text") or "")
    if created_titles and not any(title in reminder_text for title in created_titles):
        return SmokeCaseResult(
            "reminder proactive push regression",
            SmokeStatus.FAIL,
            "push missing created reminder title",
        )
    return SmokeCaseResult("reminder proactive push regression", SmokeStatus.PASS, 'message_tag="reminder" received')


async def _case_heartbeat_push(smoke: SmokeSession, tasks_payload: dict[str, Any]) -> SmokeCaseResult:
    enabled, interval = _extract_interval_minutes(tasks_payload, "heartbeat")
    if not enabled:
        return SmokeCaseResult("heartbeat proactive push", SmokeStatus.SKIP, "tasks.heartbeat.enabled != true")
    if interval is None:
        return SmokeCaseResult("heartbeat proactive push", SmokeStatus.SKIP, "tasks.heartbeat.interval_minutes missing")
    if interval != 1:
        return SmokeCaseResult(
            "heartbeat proactive push",
            SmokeStatus.SKIP,
            f"tasks.heartbeat.interval_minutes={interval}, expected 1 for smoke",
        )

    # Force one deterministic abnormal signal for smoke: overdue active reminder.
    overdue_title = f"m9_smoke_overdue_{int(time.time())}"
    subprocess.run(
        [
            "sqlite3",
            "memory/hypo.db",
            (
                "INSERT INTO reminders("
                "title,description,schedule_type,schedule_value,channel,status,created_at,updated_at,next_run_at,heartbeat_config"
                ") VALUES ("
                f"'{overdue_title}','smoke overdue seed','once','2000-01-01T00:00:00+00:00','all','active',"
                "datetime('now'),datetime('now'),'2000-01-01T00:00:00+00:00',NULL"
                ");"
            ),
        ],
        check=False,
    )

    timeout = int(max(75, interval * 80))
    payload = await smoke.wait_for_tag("heartbeat", timeout=timeout)
    if payload is None:
        return SmokeCaseResult("heartbeat proactive push", SmokeStatus.FAIL, "no heartbeat push in time window")
    return SmokeCaseResult("heartbeat proactive push", SmokeStatus.PASS, 'message_tag="heartbeat" received')


def _case_message_tag_integrity(smoke: SmokeSession) -> SmokeCaseResult:
    if not smoke.proactive_payloads:
        return SmokeCaseResult("proactive message_tag integrity", SmokeStatus.SKIP, "no proactive payload observed")

    allowed_tags = {"reminder", "heartbeat", "email_scan", "tool_status"}
    for payload in smoke.proactive_payloads:
        tag = str(payload.get("message_tag") or "").strip()
        if not tag:
            return SmokeCaseResult("proactive message_tag integrity", SmokeStatus.FAIL, "message_tag is empty")
        if tag not in allowed_tags:
            return SmokeCaseResult(
                "proactive message_tag integrity",
                SmokeStatus.FAIL,
                f"unexpected message_tag={tag}",
            )
    return SmokeCaseResult(
        "proactive message_tag integrity",
        SmokeStatus.PASS,
        f"checked={len(smoke.proactive_payloads)}",
    )


async def _case_email_scan_trigger(smoke: SmokeSession, tasks_payload: dict[str, Any]) -> SmokeCaseResult:
    enabled, interval = _extract_interval_minutes(tasks_payload, "email_scan")
    if not enabled:
        return SmokeCaseResult("email_scan scheduled trigger", SmokeStatus.SKIP, "tasks.email_scan.enabled != true")
    if interval is None:
        return SmokeCaseResult("email_scan scheduled trigger", SmokeStatus.SKIP, "tasks.email_scan.interval_minutes missing")

    trigger_cmd = str(
        (
            tasks_payload.get("smoke") if isinstance(tasks_payload.get("smoke"), dict) else {}
        ).get("email_scan_trigger_cmd")
        or ""
    ).strip()
    if trigger_cmd:
        run_result = subprocess.run(trigger_cmd, shell=True, capture_output=True, text=True, check=False)
        if run_result.returncode != 0:
            detail = (run_result.stderr or run_result.stdout or "").strip()
            if len(detail) > 120:
                detail = detail[:120] + "..."
            return SmokeCaseResult(
                "email_scan scheduled trigger",
                SmokeStatus.FAIL,
                f"trigger command failed: {detail or run_result.returncode}",
            )

    payload = await smoke.wait_for_tag("email_scan", timeout=max(90, interval * 90))
    if payload is None:
        return SmokeCaseResult("email_scan scheduled trigger", SmokeStatus.FAIL, "no email_scan push in time window")
    return SmokeCaseResult("email_scan scheduled trigger", SmokeStatus.PASS, 'message_tag="email_scan" received')


async def _case_qq_non_whitelist_user_mock_async() -> SmokeCaseResult:
    class _DummyPipeline:
        def __init__(self) -> None:
            self.called = False

        async def enqueue_user_message(self, inbound, *, emit) -> None:
            del inbound, emit
            self.called = True

    from hypo_agent.channels.qq_channel import QQChannelService

    service = QQChannelService(
        napcat_http_url="http://localhost:3000",
        bot_qq="123456789",
        allowed_users={"10001"},
    )

    pipeline = _DummyPipeline()
    accepted = await service.handle_onebot_event(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": "10002",
            "message": "hello",
        },
        pipeline=pipeline,
    )

    if accepted:
        return SmokeCaseResult("qq mock non-whitelist reject", SmokeStatus.FAIL, "unexpectedly accepted")
    if pipeline.called:
        return SmokeCaseResult(
            "qq mock non-whitelist reject",
            SmokeStatus.FAIL,
            "pipeline should not be called",
        )
    return SmokeCaseResult("qq mock non-whitelist reject", SmokeStatus.PASS)


def _case_qq_non_whitelist_user_mock() -> SmokeCaseResult:
    return asyncio.run(_case_qq_non_whitelist_user_mock_async())


async def _case_qq_send_private_api_mock_async() -> SmokeCaseResult:
    from hypo_agent.channels.qq_adapter import QQAdapter

    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    captured: list[tuple[str, dict[str, Any]]] = []

    def fake_post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append((path, payload))
        return {"status": "ok", "data": {"message_id": 1}}

    adapter._post_json = fake_post_json  # type: ignore[method-assign]
    sent = await adapter.send_private_text(user_id="10001", text="hello")
    if not sent:
        return SmokeCaseResult("qq mock send_private_msg api", SmokeStatus.FAIL, "adapter returned false")
    if not captured:
        return SmokeCaseResult("qq mock send_private_msg api", SmokeStatus.FAIL, "api not called")
    if captured[0][0] != "/send_private_msg":
        return SmokeCaseResult(
            "qq mock send_private_msg api",
            SmokeStatus.FAIL,
            f"unexpected path={captured[0][0]}",
        )
    return SmokeCaseResult("qq mock send_private_msg api", SmokeStatus.PASS)


def _case_qq_send_private_api_mock() -> SmokeCaseResult:
    return asyncio.run(_case_qq_send_private_api_mock_async())


async def cmd_smoke(*, port: int, session_id: str) -> int:
    token = _load_token()
    uri = _ws_url(port, token)
    tasks_payload = _load_tasks_config()

    print("=" * 60)
    print("SMOKE TEST: Hypo-Agent m9 smoke gates")
    print("=" * 60)

    results: list[SmokeCaseResult] = []
    async with websockets.connect(uri) as ws:
        smoke = SmokeSession(ws=ws, session_id=session_id)
        print("[CASE 1] base dialogue regression")
        # Some providers can be slow or cold-start; keep smoke robust.
        results.append(await _case_send_regression(smoke, "你好", timeout=90))
        print("[CASE 2] reminder regression and proactive push")
        results.append(await _case_reminder_push_regression(smoke))
        print("[CASE 3] heartbeat proactive push")
        results.append(await _case_heartbeat_push(smoke, tasks_payload))
        print("[CASE 4] proactive message_tag integrity")
        results.append(_case_message_tag_integrity(smoke))
        print("[CASE 5] email_scan scheduled trigger")
        results.append(await _case_email_scan_trigger(smoke, tasks_payload))

    print("=" * 60)
    has_fail = False
    for result in results:
        _print_case_result(result)
        if result.status == SmokeStatus.FAIL:
            has_fail = True
    print("=" * 60)
    if not has_fail:
        print("ALL SMOKE TESTS PASSED")
        return 0
    print("SMOKE TEST FAILED")
    return 1


def main() -> None:
    from hypo_agent.core.config_loader import get_port

    parser = argparse.ArgumentParser(description="Hypo-Agent CLI")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Gateway port (default: $HYPO_PORT or 8765)",
    )
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
    port = args.port if args.port is not None else get_port()

    if args.command == "send":
        raise SystemExit(
            asyncio.run(
                cmd_send(
                    args.text,
                    port=port,
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
                    port=port,
                    session_id=args.session_id,
                )
            )
        )
    if args.command == "check-db":
        raise SystemExit(cmd_check_db(args.query))
    if args.command == "smoke":
        raise SystemExit(asyncio.run(cmd_smoke(port=port, session_id=args.session_id)))


if __name__ == "__main__":
    main()
