#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml


async def main() -> None:
    secrets_path = Path("config/secrets.yaml")
    with secrets_path.open(encoding="utf-8") as handle:
        secrets = yaml.safe_load(handle) or {}

    notion_config = ((secrets.get("services") or {}).get("notion") or {})
    secret = str(notion_config.get("integration_secret") or "")

    print(f"Secret loaded: {bool(secret)}")
    print(f"Secret prefix: {secret[:8]}..." if secret else "EMPTY")
    print(f"Secret length: {len(secret)}")
    print(f"Has whitespace: {secret != secret.strip()}")
    has_quotes = secret.startswith('"') or secret.startswith("'")
    print(f"Has quotes: {has_quotes}")

    from notion_client import AsyncClient

    clean_secret = secret.strip().strip('"').strip("'")
    print(f"\nClean secret prefix: {clean_secret[:8]}..." if clean_secret else "\nEMPTY")

    client = AsyncClient(
        options={
            "auth": clean_secret,
            "notion_version": "2022-06-28",
            "timeout_ms": 30_000,
        }
    )

    request = client._build_request("GET", "users/me", None, None, None, None)
    print(f"Authorization header present: {bool(request.headers.get('Authorization'))}")
    print(f"Notion-Version header: {request.headers.get('Notion-Version')}")

    try:
        result = await client.search(query="test", page_size=1)
        print(f"\n✅ Search OK! Got {len(result.get('results', []))} results")
    except Exception as exc:
        print(f"\n❌ Search failed: {type(exc).__name__}: {exc}")

    try:
        me = await client.users.me()
        print(f"✅ Auth OK! Bot user: {me.get('name', 'unknown')}")
    except Exception as exc:
        print(f"❌ Auth failed: {type(exc).__name__}: {exc}")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
