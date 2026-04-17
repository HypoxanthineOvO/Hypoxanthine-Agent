from __future__ import annotations

from pathlib import Path
import hashlib
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

logger = structlog.get_logger("hypo_agent.skills.subscription.wbi")

MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]
_WBI_CACHE: dict[str, tuple[float, str, str]] = {}
_CACHE_TTL_SECONDS = 1800
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=15.0)


def get_mixin_key(img_key: str, sub_key: str) -> str:
    orig = f"{img_key}{sub_key}"
    if len(orig) <= max(MIXIN_KEY_ENC_TAB):
        return orig[:32]
    return "".join(orig[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sanitize_params(params: dict[str, Any]) -> dict[str, str | int]:
    cleaned: dict[str, str | int] = {}
    for key, value in sorted(params.items()):
        if key == "wts":
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                cleaned[key] = int(time.time())
            continue
        string_value = str(value)
        cleaned[key] = "".join(ch for ch in string_value if ch not in "!'()*")
    return cleaned


def sign_params(
    params: dict[str, Any],
    img_key: str,
    sub_key: str,
) -> dict[str, str | int]:
    mixin_key = get_mixin_key(img_key, sub_key)
    signed = sanitize_params({**params, "wts": int(time.time())})
    query = urlencode(signed)
    signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return signed


async def get_wbi_keys(
    cookie: str,
    client: httpx.AsyncClient | None = None,
    *,
    transport: Any | None = None,
) -> tuple[str, str]:
    cache_key = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    now = time.time()
    cached = _WBI_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1], cached[2]

    owns_client = client is None
    resolved_client = client
    if resolved_client is None:
        resolved_client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Cookie": cookie,
            },
        )
    try:
        response = await resolved_client.get("https://api.bilibili.com/x/web-interface/nav")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        wbi_img = data.get("wbi_img") or {}
        img_key = Path(str(wbi_img.get("img_url") or "")).stem
        sub_key = Path(str(wbi_img.get("sub_url") or "")).stem
        if not img_key or not sub_key:
            raise RuntimeError("Failed to extract Wbi keys")
        _WBI_CACHE[cache_key] = (now, img_key, sub_key)
        logger.info("subscription.wbi.keys_refreshed")
        return img_key, sub_key
    finally:
        if owns_client:
            await resolved_client.aclose()
