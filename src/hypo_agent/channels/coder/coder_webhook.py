from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hypo_agent.models import Message

router = APIRouter()


@router.post("/api/coder/webhook")
async def coder_webhook(request: Request) -> JSONResponse:
    secret = str(getattr(request.app.state, "coder_webhook_secret", "") or "").strip()
    if not secret:
        return JSONResponse(status_code=404, content={"detail": "coder webhook not enabled"})

    body = await request.body()
    event_header = str(request.headers.get("X-HypoCoder-Event") or "").strip()
    signature = str(request.headers.get("X-HypoCoder-Signature") or "").strip()
    if not event_header or not _signature_valid(secret, body, signature):
        return JSONResponse(status_code=403, content={"detail": "invalid webhook signature"})

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"detail": "invalid json"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"detail": "invalid payload"})

    event = str(payload.get("event") or "").strip()
    if not event or event != event_header:
        return JSONResponse(status_code=400, content={"detail": "event mismatch"})

    structured_store = getattr(request.app.state, "structured_store", None)
    task_id = str(payload.get("taskId") or "").strip()
    task_row: dict[str, Any] | None = None
    if structured_store is not None and task_id:
        getter = getattr(structured_store, "get_coder_task", None)
        if callable(getter):
            result = getter(task_id)
            task_row = await result if hasattr(result, "__await__") else result
        updater = getattr(structured_store, "update_coder_task_status", None)
        if callable(updater):
            status, last_error = _task_status_from_event(payload)
            result = updater(task_id=task_id, status=status, last_error=last_error)
            if hasattr(result, "__await__"):
                await result

    text = _format_event_message(payload)
    callback = getattr(getattr(request.app.state, "pipeline", None), "on_proactive_message", None)
    session_id = str(task_row.get("session_id") or "").strip() if isinstance(task_row, dict) else ""
    attached = bool(int(task_row.get("attached") or 0)) if isinstance(task_row, dict) else False
    if callback is not None and text and session_id and attached:
        result = callback(
            Message(
                text=text,
                sender="hypo-coder",
                session_id=session_id,
                channel="system",
                message_tag="tool_status",
                metadata={"source": "hypo_coder", "task_id": task_id},
            )
        )
        if hasattr(result, "__await__"):
            await result

    return JSONResponse(status_code=200, content={"status": "ok"})


def _signature_valid(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    normalized = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, normalized)


def _format_event_message(payload: dict[str, Any]) -> str:
    event = str(payload.get("event") or "").strip()
    task_id = str(payload.get("taskId") or "").strip()
    if event == "task.completed":
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        summary = str(result.get("summary") or "暂无摘要").strip() or "暂无摘要"
        changes = result.get("fileChanges") if isinstance(result.get("fileChanges"), list) else []
        tests_passed = result.get("testsPassed")
        tests_text = "通过" if tests_passed is True else "失败" if tests_passed is False else "未知"
        return "\n".join(
            [
                "编码任务完成！",
                f"任务：{task_id}",
                f"摘要：{summary}",
                f"文件变更：{len(changes)} 个文件",
                f"测试：{tests_text}",
            ]
        )
    if event == "task.failed":
        error = str(payload.get("error") or "未知错误").strip() or "未知错误"
        return f"编码任务失败：{error}"
    if event == "task.approval_required":
        command = str(payload.get("command") or payload.get("message") or "未知操作").strip() or "未知操作"
        return f"Hypo-Coder 需要你审批一个操作：{command}"
    return f"Hypo-Coder 事件：{event}"


def _task_status_from_event(payload: dict[str, Any]) -> tuple[str, str]:
    event = str(payload.get("event") or "").strip()
    if event == "task.completed":
        return "completed", ""
    if event == "task.failed":
        return "failed", str(payload.get("error") or "未知错误").strip() or "未知错误"
    if event == "task.approval_required":
        return "approval_required", ""
    return event.replace("task.", "").strip() or "unknown"
