from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from hypo_agent.core.config_loader import (  # noqa: E402
    ResolvedModelConfig,
    load_runtime_model_config,
)

try:
    from litellm import acompletion
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit(
        "litellm is not installed. Install project dependencies first."
    ) from exc


PING_MESSAGES = [{"role": "user", "content": "ping"}]


@dataclass
class CheckRow:
    model_name: str
    provider: str
    status: str
    latency_ms: str
    error_info: str


async def check_model(model_name: str, cfg: ResolvedModelConfig) -> CheckRow:
    start = time.perf_counter()

    if not cfg.litellm_model:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return CheckRow(
            model_name=model_name,
            provider=cfg.provider or "N/A",
            status="❌",
            latency_ms=str(latency_ms),
            error_info="litellm_model is null",
        )

    try:
        await acompletion(
            model=cfg.litellm_model,
            api_base=cfg.api_base,
            api_key=cfg.api_key,
            messages=PING_MESSAGES,
            max_tokens=5,
            temperature=0,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return CheckRow(
            model_name=model_name,
            provider=cfg.provider or "N/A",
            status="✅",
            latency_ms=str(latency_ms),
            error_info="",
        )
    except Exception as exc:  # pragma: no cover - depends on remote providers
        latency_ms = int((time.perf_counter() - start) * 1000)
        return CheckRow(
            model_name=model_name,
            provider=cfg.provider or "N/A",
            status="❌",
            latency_ms=str(latency_ms),
            error_info=str(exc).strip().splitlines()[0][:120],
        )


def print_table(rows: list[CheckRow]) -> None:
    headers = ["模型名", "Provider", "状态", "延迟(ms)", "错误信息"]
    data_rows = [
        [row.model_name, row.provider, row.status, row.latency_ms, row.error_info]
        for row in rows
    ]
    widths = [
        max(len(headers[idx]), *(len(row[idx]) for row in data_rows))
        for idx in range(len(headers))
    ]

    def _fmt(columns: list[str]) -> str:
        return " | ".join(
            columns[idx].ljust(widths[idx]) for idx in range(len(columns))
        )

    separator = "-+-".join("-" * width for width in widths)
    print(_fmt(headers))
    print(separator)
    for row in data_rows:
        print(_fmt(row))


async def main() -> int:
    runtime_config = load_runtime_model_config(
        REPO_ROOT / "config/models.yaml",
        REPO_ROOT / "config/secrets.yaml",
    )

    candidates: list[tuple[str, ResolvedModelConfig]] = [
        (name, cfg)
        for name, cfg in runtime_config.models.items()
        if cfg.provider is not None
    ]
    candidates.sort(key=lambda item: item[0])

    if not candidates:
        print("No configured models with non-null provider.")
        return 0

    rows = [await check_model(name, cfg) for name, cfg in candidates]
    print_table(rows)
    return 1 if any(row.status == "❌" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
