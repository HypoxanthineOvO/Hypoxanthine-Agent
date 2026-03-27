from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tempfile

from PIL import Image

from probe_client.platform.common import collect_process_list, is_black_frame, jpeg_bytes_to_base64

_IDLE_PATTERN = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def take_screenshot(*, quality: int = 85) -> dict:
    del quality  # screencapture already writes jpg; keep signature aligned with other platforms
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "jpg", str(path)],
            check=True,
            capture_output=True,
            text=False,
        )
        payload = path.read_bytes()
        with Image.open(path) as image:
            width, height = image.size
        return {
            "image_base64": jpeg_bytes_to_base64(payload),
            "width": width,
            "height": height,
            "black_frame": is_black_frame(payload),
        }
    finally:
        path.unlink(missing_ok=True)


def is_idle(*, idle_seconds: int = 60) -> bool:
    result = subprocess.run(
        ["ioreg", "-c", "IOHIDSystem"],
        check=False,
        capture_output=True,
        text=True,
    )
    match = _IDLE_PATTERN.search(result.stdout)
    if match is None:
        return False
    idle_ns = int(match.group(1))
    return idle_ns >= int(idle_seconds) * 1_000_000_000


def get_process_list(top_n: int) -> list[dict]:
    return collect_process_list(top_n)
