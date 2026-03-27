from __future__ import annotations

from pathlib import Path
from typing import Any

from hypo_agent.channels.probe.probe_server import ProbeRPCError, ProbeServer
from hypo_agent.models import Attachment, SkillOutput
from hypo_agent.skills.base import BaseSkill


class ProbeSkill(BaseSkill):
    name = "probe"
    description = "List probe devices, request screenshots, inspect process lists, and read screenshot history."
    required_permissions: list[str] = []

    def __init__(self, *, probe_server: ProbeServer) -> None:
        self.probe_server = probe_server

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "probe_list_devices",
                    "description": "列出所有已注册的探针设备及其在线状态。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "probe_screenshot",
                    "description": "对指定设备立即截图并返回图片。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string"},
                        },
                        "required": ["device_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "probe_process_list",
                    "description": "查询指定设备当前运行的进程列表。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string"},
                            "top_n": {"type": "integer", "default": 20},
                        },
                        "required": ["device_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "probe_list_screenshots",
                    "description": "列出指定设备某天的截图记录（含空闲时段）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string"},
                            "date": {
                                "type": "string",
                                "description": "Date in YYYY-MM-DD format. Defaults to today (UTC).",
                            },
                        },
                        "required": ["device_id"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        if tool_name == "probe_list_devices":
            return await self._list_devices()
        if tool_name == "probe_screenshot":
            return await self._capture_screenshot(params)
        if tool_name == "probe_process_list":
            return await self._get_process_list(params)
        if tool_name == "probe_list_screenshots":
            return await self._list_screenshots(params)
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}' for probe skill")

    async def _list_devices(self) -> SkillOutput:
        items = self.probe_server.list_devices()
        metadata = {}
        if not items:
            metadata["message"] = "目前没有在线的探针设备"
        return SkillOutput(status="success", result=items, metadata=metadata)

    async def _capture_screenshot(self, params: dict[str, Any]) -> SkillOutput:
        device_id = str(params.get("device_id") or "").strip()
        if not device_id:
            return SkillOutput(status="error", error_info="device_id is required")
        try:
            payload = await self.probe_server.send_rpc(device_id, "screenshot", {})
        except ProbeRPCError as exc:
            return SkillOutput(status="error", error_info=str(exc))

        if bool(payload.get("idle")):
            return SkillOutput(status="success", result="设备当前处于息屏/空闲状态")

        stored = self.probe_server.store_screenshot(
            device_id=device_id,
            timestamp=payload.get("timestamp"),
            idle=False,
            image_base64=str(payload.get("image_base64") or ""),
        )
        image_path = Path(str(stored["path"] or "")).resolve(strict=False)
        attachment = Attachment(
            type="image",
            url=str(image_path),
            filename=image_path.name,
            mime_type="image/jpeg",
            size_bytes=image_path.stat().st_size,
        )
        return SkillOutput(
            status="success",
            result=f"已获取 {device_id} 的实时截图",
            metadata={
                "device_id": device_id,
                "timestamp": stored["timestamp"],
                "width": payload.get("width"),
                "height": payload.get("height"),
            },
            attachments=[attachment],
        )

    async def _get_process_list(self, params: dict[str, Any]) -> SkillOutput:
        device_id = str(params.get("device_id") or "").strip()
        if not device_id:
            return SkillOutput(status="error", error_info="device_id is required")
        raw_top_n = params.get("top_n", 20)
        try:
            top_n = max(1, int(raw_top_n))
        except (TypeError, ValueError):
            return SkillOutput(status="error", error_info="top_n must be an integer")
        try:
            payload = await self.probe_server.send_rpc(
                device_id,
                "process_list",
                {"top_n": top_n},
            )
        except ProbeRPCError as exc:
            return SkillOutput(status="error", error_info=str(exc))
        items = payload if isinstance(payload, list) else payload.get("items", [])
        if not isinstance(items, list):
            items = []
        if not items:
            return SkillOutput(status="success", result=f"{device_id} 当前没有可报告的进程信息。")
        lines = [f"{device_id} 当前进程（按 CPU 排序，top {top_n}）:"]
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- PID {pid} | {name} | CPU {cpu:.1f}% | RAM {ram:.0f} MB".format(
                    pid=int(item.get("pid") or 0),
                    name=str(item.get("name") or "unknown"),
                    cpu=float(item.get("cpu_percent") or 0.0),
                    ram=float(item.get("ram_mb") or 0.0),
                )
            )
        return SkillOutput(status="success", result="\n".join(lines))

    async def _list_screenshots(self, params: dict[str, Any]) -> SkillOutput:
        device_id = str(params.get("device_id") or "").strip()
        if not device_id:
            return SkillOutput(status="error", error_info="device_id is required")
        date_text = str(params.get("date") or "").strip() or None
        try:
            items = self.probe_server.list_screenshots(device_id, date=date_text)
        except (OSError, ValueError, TypeError) as exc:
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="success", result=items)
