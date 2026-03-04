#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from litellm import acompletion

from hypo_agent.core.config_loader import RuntimeModelConfig, load_runtime_model_config
from hypo_agent.core.model_connectivity import ModelProbeResult, probe_model


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check model connectivity and tool calling support for configured models."
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model names. Empty means all configured runnable models.",
    )
    parser.add_argument(
        "--models-path",
        default="config/models.yaml",
        help="Path to models.yaml",
    )
    parser.add_argument(
        "--secrets-path",
        default="config/secrets.yaml",
        help="Path to secrets.yaml",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout per model probe in seconds (default: 30)",
    )
    parser.add_argument(
        "--tool-choice",
        default="auto",
        choices=["auto", "required", "none"],
        help="tool_choice passed to LLM tool calling",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in output.",
    )
    return parser.parse_args()


def _select_models(config: RuntimeModelConfig, requested: str) -> list[str]:
    all_runnable = [
        name
        for name, model in config.models.items()
        if model.provider is not None and model.litellm_model is not None
    ]
    if not requested.strip():
        return all_runnable

    selected: list[str] = []
    for raw in requested.split(","):
        model_name = raw.strip()
        if not model_name:
            continue
        if model_name not in config.models:
            raise ValueError(f"Unknown model: {model_name}")
        selected.append(model_name)
    return selected


def _truncate(text: str, *, max_len: int = 96) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _colorize(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths: list[int] = []
    for idx, header in enumerate(headers):
        col_width = len(header)
        for row in rows:
            col_width = max(col_width, len(row[idx]))
        widths.append(col_width)

    def build_border(char: str = "-") -> str:
        return "+" + "+".join(char * (w + 2) for w in widths) + "+"

    def build_row(items: list[str]) -> str:
        cells = [f" {item.ljust(widths[i])} " for i, item in enumerate(items)]
        return "|" + "|".join(cells) + "|"

    lines = [build_border("-"), build_row(headers), build_border("=")]
    for row in rows:
        lines.append(build_row(row))
    lines.append(build_border("-"))
    return "\n".join(lines)


async def _probe_models(
    config: RuntimeModelConfig,
    model_names: list[str],
    *,
    timeout_seconds: float,
    tool_choice: str,
) -> list[ModelProbeResult]:
    results: list[ModelProbeResult] = []
    for model_name in model_names:
        cfg = config.models[model_name]
        result = await probe_model(
            model_name,
            cfg,
            acompletion_fn=acompletion,
            timeout_seconds=timeout_seconds,
            tool_choice=tool_choice,
        )
        results.append(result)
    return results


def _build_detail(result: ModelProbeResult) -> str:
    if result.error:
        return _truncate(result.error)
    if result.tool_calls_count > 0:
        return f"tool_calls={result.tool_calls_count}"
    return "tool_calls=0"


def _status_text(result: ModelProbeResult) -> tuple[str, str]:
    connect = "OK" if result.connectivity_ok else "FAIL"
    if result.tool_calling_ok is None:
        tool = "N/A"
    elif result.tool_calling_ok:
        tool = "OK"
    else:
        tool = "NO_CALL"
    return connect, tool


def _print_report(
    config: RuntimeModelConfig,
    model_names: list[str],
    results: list[ModelProbeResult],
    *,
    color_enabled: bool,
) -> None:
    print("Model Connectivity Report")
    print(f"default_model : {config.default_model}")
    print(f"chat_route    : {config.task_routing.get('chat')}")
    print(f"models_checked: {len(model_names)}")
    print("")

    rows: list[list[str]] = []
    for result in results:
        connect, tool = _status_text(result)
        if connect == "OK":
            connect = _colorize(connect, "32", enabled=color_enabled)
        else:
            connect = _colorize(connect, "31", enabled=color_enabled)

        if tool == "OK":
            tool = _colorize(tool, "32", enabled=color_enabled)
        elif tool == "NO_CALL":
            tool = _colorize(tool, "33", enabled=color_enabled)
        else:
            tool = _colorize(tool, "90", enabled=color_enabled)

        rows.append(
            [
                result.model_name,
                result.litellm_model or "-",
                result.provider or "-",
                connect,
                tool,
                f"{result.latency_ms:.1f}",
                _build_detail(result),
            ]
        )

    headers = [
        "Model",
        "LiteLLM Model",
        "Provider",
        "Connect",
        "ToolCall",
        "Latency(ms)",
        "Detail",
    ]
    print(_render_table(headers, rows))

    total_fail = sum(1 for r in results if not r.connectivity_ok)
    total_no_call = sum(
        1
        for r in results
        if r.connectivity_ok and (r.tool_calling_ok is False)
    )
    print("")
    print(
        "Summary: "
        f"connect_fail={total_fail}, "
        f"tool_no_call={total_no_call}, "
        f"ok={len(results) - total_fail - total_no_call}"
    )


def _exit_code(results: list[ModelProbeResult]) -> int:
    if any(not result.connectivity_ok for result in results):
        return 1
    if any(result.tool_calling_ok is False for result in results):
        return 2
    return 0


def main() -> int:
    args = _parse_args()
    config = load_runtime_model_config(args.models_path, args.secrets_path)
    model_names = _select_models(config, args.models)
    if not model_names:
        print("No runnable models found (provider/litellm_model missing).")
        return 1

    results = asyncio.run(
        _probe_models(
            config,
            model_names,
            timeout_seconds=args.timeout,
            tool_choice=args.tool_choice,
        )
    )
    color_enabled = (not args.no_color) and sys.stdout.isatty()
    _print_report(config, model_names, results, color_enabled=color_enabled)
    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
