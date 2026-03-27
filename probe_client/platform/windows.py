from __future__ import annotations

import ctypes
from ctypes import wintypes
from io import BytesIO

from mss import mss
from PIL import Image

from probe_client.platform.common import collect_process_list, is_black_frame, jpeg_bytes_to_base64


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def take_screenshot(*, quality: int = 85) -> dict:
    with mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=max(1, min(95, int(quality))))
        payload = buffer.getvalue()
        return {
            "image_base64": jpeg_bytes_to_base64(payload),
            "width": image.width,
            "height": image.height,
            "black_frame": is_black_frame(payload),
        }


def is_idle(*, idle_seconds: int = 60) -> bool:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return False
    elapsed_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return elapsed_ms >= int(idle_seconds) * 1000


def get_process_list(top_n: int) -> list[dict]:
    return collect_process_list(top_n)
