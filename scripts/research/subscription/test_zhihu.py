#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from _common import get_env_or_secret, mask_cookie_keys, merge_cookie_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate zhihu member activity endpoints.")
    parser.add_argument("--user-id", default="zhang-jia-wei", help="知乎 user_id / url_token")
    parser.add_argument("--count", type=int, default=5, help="打印条目数量")
    return parser


def build_client(user_id: str) -> tuple[httpx.Client, dict[str, str]]:
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.zhihu.com/people/{user_id}/activities",
        "Accept": "application/json, text/plain, */*",
    }
    client = httpx.Client(headers=headers, cookies=cookies, timeout=20.0, follow_redirects=True)
    return client, cookies


def ts_to_iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def describe_activities(payload: dict[str, Any], *, count: int) -> None:
    items = payload.get("data") or []
    print(f"activities={len(items)}")
    for item in items[:count]:
        target = item.get("target") or {}
        print(
            json.dumps(
                {
                    "verb": item.get("verb"),
                    "created_time": item.get("created_time"),
                    "action_text": item.get("action_text"),
                    "target_type": target.get("type"),
                    "target_id": target.get("id"),
                    "title": target.get("title"),
                    "excerpt": target.get("excerpt"),
                    "url": target.get("url"),
                },
                ensure_ascii=False,
            )
        )


def describe_pins(payload: dict[str, Any], *, count: int) -> None:
    items = payload.get("data") or []
    print(f"pins={len(items)}")
    for item in items[:count]:
        author = item.get("author") or {}
        print(
            json.dumps(
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "created": item.get("created"),
                    "created_iso": ts_to_iso(item.get("created")),
                    "updated": item.get("updated"),
                    "excerpt_title": item.get("excerpt_title"),
                    "author": author.get("name"),
                    "url_token": author.get("url_token"),
                    "url": item.get("url"),
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    args = build_parser().parse_args()
    client, cookies = build_client(args.user_id)
    print(f"cookie_keys={mask_cookie_keys(cookies)}")
    try:
        activity_response = client.get(
            f"https://www.zhihu.com/api/v4/members/{args.user_id}/activities",
            params={"limit": max(args.count, 1), "offset": 0},
        )
        print("\n[activities]")
        print(
            json.dumps(
                {
                    "http_status": activity_response.status_code,
                    "content_type": activity_response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        if "application/json" in str(activity_response.headers.get("content-type")):
            activity_payload = activity_response.json()
            if "error" in activity_payload:
                print(json.dumps(activity_payload["error"], ensure_ascii=False))
            else:
                describe_activities(activity_payload, count=args.count)
        else:
            print(activity_response.text[:500])

        pins_response = client.get(
            f"https://www.zhihu.com/api/v4/members/{args.user_id}/pins",
            params={"limit": max(args.count, 1), "offset": 0},
        )
        print("\n[pins]")
        print(
            json.dumps(
                {
                    "http_status": pins_response.status_code,
                    "content_type": pins_response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        if "application/json" in str(pins_response.headers.get("content-type")):
            pins_payload = pins_response.json()
            if "error" in pins_payload:
                print(json.dumps(pins_payload["error"], ensure_ascii=False))
            else:
                describe_pins(pins_payload, count=args.count)
        else:
            print(pins_response.text[:500])

        article_response = client.get(f"https://www.zhihu.com/api/v4/articles/254930530")
        print("\n[article_probe]")
        print(
            json.dumps(
                {
                    "http_status": article_response.status_code,
                    "content_type": article_response.headers.get("content-type"),
                },
                ensure_ascii=False,
            )
        )
        if "application/json" in str(article_response.headers.get("content-type")):
            article_payload = article_response.json()
            print(json.dumps(article_payload, ensure_ascii=False)[:500])
        else:
            print(article_response.text[:500])

        for atom_url in (
            f"https://www.zhihu.com/people/{args.user_id}/activities/rss",
            f"https://www.zhihu.com/people/{args.user_id}/posts/rss",
        ):
            atom_response = client.get(atom_url)
            print(f"\n[atom_probe] {atom_url}")
            print(
                json.dumps(
                    {
                        "http_status": atom_response.status_code,
                        "content_type": atom_response.headers.get("content-type"),
                    },
                    ensure_ascii=False,
                )
            )
            print(atom_response.text[:300].replace("\n", " "))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
