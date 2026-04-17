#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from typing import Any

import httpx

from _common import get_env_or_secret, mask_cookie_keys, merge_cookie_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate weibo user feed endpoints.")
    parser.add_argument("--uid", default="1195230310", help="微博 UID")
    parser.add_argument("--count", type=int, default=5, help="打印条目数量")
    return parser


def build_client(uid: str) -> tuple[httpx.Client, dict[str, str]]:
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
            "SSOLoginState": "SSOLOGINSTATE",
            "_T_WM": "T_WM",
            "XSRF-TOKEN": "XSRF_TOKEN",
        },
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1"
        ),
        "Referer": f"https://m.weibo.cn/u/{uid}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    client = httpx.Client(headers=headers, cookies=cookies, timeout=20.0, follow_redirects=True)
    return client, cookies


def html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def describe_mobile_cards(payload: dict[str, Any], *, count: int) -> None:
    data = payload.get("data") or {}
    cards = data.get("cards") or []
    printed = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        mblog = card.get("mblog") or {}
        if not isinstance(mblog, dict) or not mblog:
            continue
        print(
            json.dumps(
                {
                    "id": mblog.get("id"),
                    "mid": mblog.get("mid"),
                    "created_at": mblog.get("created_at"),
                    "is_retweet": bool(mblog.get("retweeted_status")),
                    "text": html_to_text(str(mblog.get("text") or "")),
                    "source": mblog.get("source"),
                    "reposts_count": mblog.get("reposts_count"),
                    "comments_count": mblog.get("comments_count"),
                    "attitudes_count": mblog.get("attitudes_count"),
                },
                ensure_ascii=False,
            )
        )
        printed += 1
        if printed >= count:
            break
    print(f"mobile_cards={printed}")


def describe_desktop_statuses(payload: dict[str, Any], *, count: int) -> None:
    statuses = payload.get("data", {}).get("list") or []
    print(f"desktop_statuses={len(statuses)}")
    for item in statuses[:count]:
        print(
            json.dumps(
                {
                    "id": item.get("id"),
                    "mblogid": item.get("mblogid"),
                    "created_at": item.get("created_at"),
                    "is_retweet": bool(item.get("retweeted_status")),
                    "text_raw": item.get("text_raw"),
                    "reposts_count": item.get("reposts_count"),
                    "comments_count": item.get("comments_count"),
                    "attitudes_count": item.get("attitudes_count"),
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    args = build_parser().parse_args()
    client, cookies = build_client(args.uid)
    print(f"cookie_keys={mask_cookie_keys(cookies)}")
    try:
        mobile_response = client.get(
            "https://m.weibo.cn/api/container/getIndex",
            params={"containerid": f"107603{args.uid}"},
        )
        print("\n[mobile_api]")
        print(
            json.dumps(
                {
                    "http_status": mobile_response.status_code,
                    "content_type": mobile_response.headers.get("content-type"),
                    "retry_after": mobile_response.headers.get("retry-after"),
                },
                ensure_ascii=False,
            )
        )
        if "application/json" in str(mobile_response.headers.get("content-type")):
            mobile_payload = mobile_response.json()
            print(
                json.dumps(
                    {
                        "ok": mobile_payload.get("ok"),
                        "msg": mobile_payload.get("msg"),
                    },
                    ensure_ascii=False,
                )
            )
            describe_mobile_cards(mobile_payload, count=args.count)
        else:
            print(mobile_response.text[:500])

        desktop_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://weibo.com/u/{args.uid}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        xsrf_token = cookies.get("XSRF-TOKEN", "").strip()
        if xsrf_token:
            desktop_headers["X-XSRF-TOKEN"] = xsrf_token
        desktop_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
        desktop_client = httpx.Client(
            headers=desktop_headers,
            timeout=20.0,
            follow_redirects=True,
        )
        try:
            desktop_response = desktop_client.get(
                "https://weibo.com/ajax/statuses/mymblog",
                params={"uid": args.uid, "page": 1, "feature": 0},
            )
        finally:
            desktop_client.close()

        print("\n[desktop_ajax]")
        print(
            json.dumps(
                {
                    "http_status": desktop_response.status_code,
                    "content_type": desktop_response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        if "application/json" in str(desktop_response.headers.get("content-type")):
            desktop_payload = desktop_response.json()
            print(
                json.dumps(
                    {
                        "ok": desktop_payload.get("ok"),
                        "url": desktop_payload.get("url"),
                    },
                    ensure_ascii=False,
                )
            )
            describe_desktop_statuses(desktop_payload, count=args.count)
        else:
            print(desktop_response.text[:500])
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
