from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from PIL import Image, ImageStat
import psutil


def jpeg_bytes_to_base64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def is_black_frame(payload: bytes, *, threshold: float = 5.0) -> bool:
    image = Image.open(BytesIO(payload)).convert("L")
    stat = ImageStat.Stat(image)
    return float(stat.stddev[0] if stat.stddev else 0.0) < float(threshold)


def collect_process_list(top_n: int) -> list[dict[str, Any]]:
    top_n = max(1, int(top_n))
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            process.cpu_percent(interval=None)
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    psutil.cpu_percent(interval=0.1)
    items: list[dict[str, Any]] = []
    for process in processes:
        try:
            memory_info = process.info.get("memory_info")
            ram_mb = (memory_info.rss / 1024 / 1024) if memory_info is not None else 0.0
            items.append(
                {
                    "pid": int(process.info.get("pid") or 0),
                    "name": str(process.info.get("name") or "unknown"),
                    "cpu_percent": float(process.cpu_percent(interval=None) or 0.0),
                    "ram_mb": round(float(ram_mb), 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    items.sort(key=lambda item: (float(item.get("cpu_percent") or 0.0), float(item.get("ram_mb") or 0.0)), reverse=True)
    return items[:top_n]
