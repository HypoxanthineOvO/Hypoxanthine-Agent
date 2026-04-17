#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

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
    parser = argparse.ArgumentParser(description="Validate subscription search APIs.")
    parser.add_argument("--bilibili-keyword", default="飓风", help="Bilibili search keyword")
    parser.add_argument("--weibo-keyword", default="草莓牛奶特别甜", help="Weibo search keyword")
    parser.add_argument("--zhihu-keyword", default="张佳玮", help="Zhihu search keyword")
    parser.add_argument("--limit", type=int, default=5, help="How many candidates to print")
    return parser


def _parse_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _print_candidates(platform: str, items: list[dict[str, Any]], *, limit: int) -> None:
    print(f"{platform}_candidates={len(items)}")
    for item in items[:limit]:
        print(json.dumps(item, ensure_ascii=False))


def _get_mixin_key(orig: str) -> str:
    return "".join(orig[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def _sanitize_params(params: dict[str, Any]) -> dict[str, str | int]:
    cleaned: dict[str, str | int] = {}
    for key, value in sorted(params.items()):
        if key == "wts":
            cleaned[key] = int(value)
            continue
        cleaned[key] = "".join(ch for ch in str(value) if ch not in "!'()*")
    return cleaned


def _sign_wbi(params: dict[str, Any], *, img_key: str, sub_key: str) -> dict[str, str | int]:
    mixin_key = _get_mixin_key(f"{img_key}{sub_key}")
    signed = _sanitize_params({**params, "wts": int(time.time())})
    query = urlencode(signed)
    signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return signed


def _build_bilibili_client() -> tuple[httpx.Client, dict[str, str]]:
    raw_cookie = get_env_or_secret(
        "BILIBILI_COOKIE",
        secret_paths=[
            "services.bilibili.cookie",
            "subscription_research.bilibili.cookie",
            "services.subscription_research.bilibili.cookie",
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
        },
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": "https://search.bilibili.com/upuser",
        "Accept": "application/json, text/plain, */*",
    }
    client = httpx.Client(headers=headers, cookies=cookies, timeout=20.0, follow_redirects=True)
    return client, cookies


def _fetch_wbi_keys(client: httpx.Client) -> tuple[str, str]:
    response = client.get("https://api.bilibili.com/x/web-interface/nav")
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_key = Path(str(wbi_img.get("img_url") or "")).stem
    sub_key = Path(str(wbi_img.get("sub_url") or "")).stem
    if not img_key or not sub_key:
        raise RuntimeError("failed to extract bilibili Wbi keys")
    return img_key, sub_key


def validate_bilibili(keyword: str, *, limit: int) -> None:
    client, cookies = _build_bilibili_client()
    print(f"[bilibili] cookie_keys={mask_cookie_keys(cookies)}")
    try:
        img_key, sub_key = _fetch_wbi_keys(client)
        signed = _sign_wbi(
            {
                "keyword": keyword,
                "search_type": "bili_user",
                "page": 1,
                "page_size": limit,
            },
            img_key=img_key,
            sub_key=sub_key,
        )
        response = client.get("https://api.bilibili.com/x/web-interface/wbi/search/type", params=signed)
        print(
            json.dumps(
                {
                    "http_status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        payload = _parse_json(response)
        if payload is None:
            print(response.text[:500])
            return
        print(json.dumps({"code": payload.get("code"), "message": payload.get("message")}, ensure_ascii=False))
        results = (payload.get("data") or {}).get("result") or []
        _print_candidates(
            "bilibili",
            [
                {
                    "mid": item.get("mid"),
                    "uname": item.get("uname"),
                    "usign": item.get("usign"),
                    "fans": item.get("fans"),
                    "videos": item.get("videos"),
                    "level": item.get("level"),
                }
                for item in results
                if isinstance(item, dict)
            ],
            limit=limit,
        )
    finally:
        client.close()


def _build_weibo_client(keyword: str) -> tuple[httpx.Client, dict[str, str]]:
    raw_cookie = get_env_or_secret(
        "WEIBO_COOKIE",
        secret_paths=[
            "services.weibo.cookie",
            "subscription_research.weibo.cookie",
            "services.subscription_research.weibo.cookie",
        ],
    )
    cookies = merge_cookie_sources(
        raw_cookie,
        env_prefix="WEIBO",
        key_map={
            "SUB": "SUB",
            "SUBP": "SUBP",
            "XSRF-TOKEN": "XSRF_TOKEN",
        },
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://s.weibo.com/user?q={quote(keyword, safe='')}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    xsrf_token = cookies.get("XSRF-TOKEN", "").strip()
    if xsrf_token:
        headers["X-XSRF-TOKEN"] = xsrf_token
    client = httpx.Client(headers=headers, cookies=cookies, timeout=20.0, follow_redirects=True)
    return client, cookies


def validate_weibo(keyword: str, *, limit: int) -> None:
    client, cookies = _build_weibo_client(keyword)
    print(f"[weibo] cookie_keys={mask_cookie_keys(cookies)}")
    try:
        response = client.get("https://weibo.com/ajax/side/search", params={"q": keyword})
        print(
            json.dumps(
                {
                    "http_status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        payload = _parse_json(response)
        if payload is None:
            print(response.text[:500])
            return
        print(json.dumps({"ok": payload.get("ok")}, ensure_ascii=False))
        data = payload.get("data") or {}
        results = (data.get("user") or []) + (data.get("users") or [])
        _print_candidates(
            "weibo",
            [
                {
                    "uid": item.get("uid") or item.get("id"),
                    "nick": item.get("nick") or item.get("screen_name"),
                    "description": item.get("desc1") or item.get("description"),
                    "followers_count": item.get("followers_count"),
                }
                for item in results
                if isinstance(item, dict)
            ],
            limit=limit,
        )
    finally:
        client.close()


def validate_zhihu(keyword: str, *, limit: int) -> None:
    raw_cookie = get_env_or_secret(
        "ZHIHU_COOKIE",
        secret_paths=[
            "services.zhihu.cookie",
            "subscription_research.zhihu.cookie",
            "services.subscription_research.zhihu.cookie",
        ],
    )
    cookies = merge_cookie_sources(
        raw_cookie,
        env_prefix="ZHIHU",
        key_map={
            "z_c0": "Z_C0",
            "d_c0": "D_C0",
            "q_c1": "Q_C1",
        },
    )
    client = httpx.Client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://www.zhihu.com/search?type=content&q={quote(keyword, safe='')}",
            "Accept": "application/json, text/plain, */*",
        },
        cookies=cookies,
        timeout=20.0,
        follow_redirects=True,
    )
    print(f"[zhihu] cookie_keys={mask_cookie_keys(cookies)}")
    try:
        response = client.get(
            "https://api.zhihu.com/search_v3",
            params={"q": keyword, "t": "people", "correction": 1, "offset": 0, "limit": limit, "lc_idx": 0},
        )
        print(
            json.dumps(
                {
                    "http_status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        payload = _parse_json(response)
        if payload is None:
            print(response.text[:500])
            return
        _print_candidates(
            "zhihu",
            [
                {
                    "url_token": (item.get("object") or {}).get("url_token"),
                    "name": (item.get("object") or {}).get("name"),
                    "headline": (item.get("object") or {}).get("headline"),
                    "follower_count": (item.get("object") or {}).get("follower_count"),
                }
                for item in (payload.get("data") or [])
                if isinstance(item, dict) and isinstance(item.get("object"), dict)
            ],
            limit=limit,
        )
    finally:
        client.close()


def main() -> int:
    args = build_parser().parse_args()
    validate_bilibili(args.bilibili_keyword, limit=args.limit)
    print()
    validate_weibo(args.weibo_keyword, limit=args.limit)
    print()
    validate_zhihu(args.zhihu_keyword, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
