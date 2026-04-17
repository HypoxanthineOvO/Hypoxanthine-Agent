#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from _common import get_env_or_secret, mask_cookie_keys, merge_cookie_sources


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate bilibili UP feed endpoints with Wbi signing.")
    parser.add_argument("--uid", default="546195", help="UP 主 UID")
    parser.add_argument("--count", type=int, default=5, help="打印条目数量")
    parser.add_argument("--burst", type=int, default=1, help="连续请求次数，用于观察风控")
    return parser


def get_mixin_key(orig: str) -> str:
    return "".join(orig[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sanitize_params(params: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in sorted(params.items()):
        string_value = str(value)
        cleaned[key] = "".join(ch for ch in string_value if ch not in "!'()*")
    return cleaned


def sign_wbi(params: dict[str, Any], mixin_key: str) -> dict[str, str]:
    signed = sanitize_params({**params, "wts": round(time.time())})
    query = urlencode(signed)
    signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return signed


def build_client(uid: str) -> tuple[httpx.Client, dict[str, str]]:
    raw_cookie = get_env_or_secret(
        "BILIBILI_COOKIE",
        secret_paths=[
            "subscription_research.bilibili.cookie",
            "services.subscription_research.bilibili.cookie",
            "services.bilibili.cookie",
        ],
    )
    cookies = merge_cookie_sources(
        raw_cookie,
        env_prefix="BILIBILI",
        key_map={
            "SESSDATA": "SESSDATA",
            "bili_jct": "BILI_JCT",
            "DedeUserID": "DEDEUSERID",
            "buvid3": "BUVID3",
            "b_nut": "B_NUT",
            "__at_once": "AT_ONCE",
        },
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://space.bilibili.com/{uid}/video",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    client = httpx.Client(headers=headers, cookies=cookies, timeout=20.0, follow_redirects=True)
    return client, cookies


def try_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def fetch_device_cookies(client: httpx.Client, uid: str) -> None:
    if "buvid3" not in client.cookies or "b_nut" not in client.cookies:
        client.get("https://www.bilibili.com/")
    client.get(f"https://space.bilibili.com/{uid}/video")


def fetch_wbi_keys(client: httpx.Client) -> tuple[str, str, str]:
    response = client.get("https://api.bilibili.com/x/web-interface/nav")
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_key = Path(str(wbi_img.get("img_url") or "")).stem
    sub_key = Path(str(wbi_img.get("sub_url") or "")).stem
    if not img_key or not sub_key:
        raise RuntimeError(f"Failed to extract Wbi keys: {json.dumps(payload, ensure_ascii=False)[:300]}")
    return img_key, sub_key, get_mixin_key(img_key + sub_key)


def describe_arc_items(payload: dict[str, Any], *, count: int) -> None:
    data = payload.get("data") or {}
    list_payload = data.get("list") or {}
    items = list_payload.get("vlist") or []
    print(f"arc_items={len(items)}")
    for item in items[:count]:
        print(
            json.dumps(
                {
                    "aid": item.get("aid"),
                    "bvid": item.get("bvid"),
                    "title": item.get("title"),
                    "created": item.get("created"),
                    "length": item.get("length"),
                },
                ensure_ascii=False,
            )
        )


def describe_dynamic_items(payload: dict[str, Any], *, count: int) -> None:
    data = payload.get("data") or {}
    items = data.get("items") or []
    print(f"dynamic_items={len(items)}")
    for item in items[:count]:
        modules = item.get("modules") or {}
        author = modules.get("module_author") or {}
        dynamic = modules.get("module_dynamic") or {}
        major = dynamic.get("major") or {}
        archive = major.get("archive") or {}
        desc = dynamic.get("desc") or {}
        print(
            json.dumps(
                {
                    "id_str": item.get("id_str"),
                    "type": item.get("type"),
                    "pub_ts": author.get("pub_ts"),
                    "author": author.get("name"),
                    "text": desc.get("text"),
                    "title": archive.get("title"),
                    "jump_url": archive.get("jump_url"),
                    "bvid": archive.get("bvid"),
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    args = build_parser().parse_args()
    client, seed_cookies = build_client(args.uid)
    print(f"cookie_keys={mask_cookie_keys(seed_cookies)}")
    try:
        fetch_device_cookies(client, args.uid)
        print(f"runtime_cookie_keys={mask_cookie_keys(dict(client.cookies))}")
        img_key, sub_key, mixin_key = fetch_wbi_keys(client)
        print(f"img_key={img_key}")
        print(f"sub_key={sub_key}")
        print(f"mixin_key={mixin_key}")

        for index in range(1, args.burst + 1):
            print(f"\n[burst {index}] arc_search")
            signed_params = sign_wbi({"mid": args.uid, "ps": max(args.count, 1), "pn": 1}, mixin_key)
            arc_response = client.get("https://api.bilibili.com/x/space/wbi/arc/search", params=signed_params)
            print(
                json.dumps(
                    {
                        "http_status": arc_response.status_code,
                        "content_type": arc_response.headers.get("content-type"),
                        "x_ratelimit_limit": arc_response.headers.get("x-ratelimit-limit"),
                        "x_ratelimit_remaining": arc_response.headers.get("x-ratelimit-remaining"),
                        "retry_after": arc_response.headers.get("retry-after"),
                    },
                    ensure_ascii=False,
                )
            )
            arc_payload = try_json(arc_response)
            if arc_payload is None:
                print(arc_response.text[:500])
            else:
                print(
                    json.dumps(
                        {"code": arc_payload.get("code"), "message": arc_payload.get("message")},
                        ensure_ascii=False,
                    )
                )
            if arc_payload and arc_payload.get("code") == 0:
                describe_arc_items(arc_payload, count=args.count)
            elif arc_payload:
                print(json.dumps(arc_payload, ensure_ascii=False)[:500])

            print(f"\n[burst {index}] dynamic")
            dynamic_response = client.get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                params={"host_mid": args.uid},
            )
            print(
                json.dumps(
                    {
                        "http_status": dynamic_response.status_code,
                        "content_type": dynamic_response.headers.get("content-type"),
                        "x_ratelimit_limit": dynamic_response.headers.get("x-ratelimit-limit"),
                        "x_ratelimit_remaining": dynamic_response.headers.get("x-ratelimit-remaining"),
                        "retry_after": dynamic_response.headers.get("retry-after"),
                    },
                    ensure_ascii=False,
                )
            )
            dynamic_payload = try_json(dynamic_response)
            if dynamic_payload is None:
                print(dynamic_response.text[:500])
            else:
                print(
                    json.dumps(
                        {
                            "code": dynamic_payload.get("code"),
                            "message": dynamic_payload.get("message"),
                        },
                        ensure_ascii=False,
                    )
                )
            if dynamic_payload and dynamic_payload.get("code") == 0:
                describe_dynamic_items(dynamic_payload, count=args.count)
            elif dynamic_payload:
                print(json.dumps(dynamic_payload, ensure_ascii=False)[:500])
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
