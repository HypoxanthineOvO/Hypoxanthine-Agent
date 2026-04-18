#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer", "rich", "litellm", "pyyaml", "anyio"]
# ///

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any
import sys

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
os.environ.setdefault("LITELLM_LOG", "ERROR")

import anyio
import litellm
import typer
import yaml
from litellm import acompletion, aembedding
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hypo_agent.gateway.app import create_app
from hypo_agent.models import Message
from hypo_agent.core.antigravity_compat import (
    is_antigravity_provider,
    transform_antigravity_tools,
)


console = Console(width=120)
app = typer.Typer(add_completion=False, help="Check configured chat and embedding models.")
litellm.suppress_debug_info = True

CHAT_ROUTE_TYPES = {"chat", "vision"}
EMBEDDING_ROUTE_TYPES = {"embedding"}
PING_MESSAGES = [{"role": "user", "content": "ping"}]
EMBEDDING_INPUT = ["Hypo-Agent embedding probe"]
TOOL_PROMPT = "Please call the echo tool with text 'ping'."
PROBE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo back the input text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]

FULL_TOOL_PROMPT = "Please call the list_directory tool with path '.' and depth 1."


def _build_request_kwargs(*, litellm_model: str | None) -> dict[str, Any]:
    model_name = str(litellm_model or "").strip().lower()
    if model_name.startswith("ollama_chat/"):
        return {"think": False}
    return {}


def _load_auth_token() -> str:
    security_path = ROOT_DIR / "config" / "security.yaml"
    payload = yaml.safe_load(security_path.read_text(encoding="utf-8")) or {}
    token = payload.get("auth_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    nested = payload.get("security") or {}
    nested_token = nested.get("auth_token")
    if isinstance(nested_token, str) and nested_token.strip():
        return nested_token.strip()
    raise ValueError("auth_token not found in config/security.yaml")


async def _load_full_probe_tools() -> list[dict[str, Any]]:
    app = create_app(auth_token=_load_auth_token())
    pipeline = app.state.pipeline
    inbound = Message(
        text="请列出当前目录。",
        sender="user",
        session_id="check-models-probe",
        channel="webui",
    )
    candidate_skills = pipeline._match_skill_candidates(inbound)
    preloaded_skill_names = (
        pipeline._match_preloaded_skill_names(inbound, candidate_skills=candidate_skills)
        if pipeline._supports_progressive_tool_disclosure()
        else set()
    )
    tools, _ = pipeline._build_exposed_tools(
        inbound=inbound,
        session_id=inbound.session_id,
        preloaded_skill_names=preloaded_skill_names,
    )
    return tools


@dataclass(slots=True)
class ProviderSecrets:
    api_base: str | None
    api_key: str | None


@dataclass(slots=True)
class ModelSpec:
    name: str
    raw_type: str
    route_type: str
    provider: str | None
    litellm_model: str | None
    api_base: str | None
    api_key: str | None
    supports_tool_calling: bool | None
    skip_reason: str | None = None
    config_error: str | None = None


@dataclass(slots=True)
class ProbeResult:
    name: str
    route_type: str
    provider: str | None
    status: str
    latency_ms: float | None
    tool_call: str
    detail: str


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return payload


def _resolve_secret_value(raw_value: Any, *, label: str) -> str | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise ValueError(f"{label} must be a string")
    value = raw_value.strip()
    if not value:
        return None
    if not value.startswith("$"):
        return value
    env_name = value[1:]
    env_value = os.getenv(env_name, "").strip()
    if not env_value:
        raise ValueError(f"Environment variable '{env_name}' is missing")
    return env_value


def _normalize_route_type(raw_type: Any) -> tuple[str, str]:
    if not isinstance(raw_type, str) or not raw_type.strip():
        return "chat", "chat"
    normalized = raw_type.strip().lower()
    if normalized in CHAT_ROUTE_TYPES:
        return normalized, "chat"
    if normalized in EMBEDDING_ROUTE_TYPES:
        return normalized, "embedding"
    return normalized, "skip"


def _load_provider_secrets(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    providers = payload.get("providers") or {}
    if not isinstance(providers, dict):
        raise ValueError("Expected 'providers' to be a mapping in secrets config")

    resolved: dict[str, dict[str, Any]] = {}
    for name, provider_payload in providers.items():
        if not isinstance(provider_payload, dict):
            raise ValueError(f"Provider '{name}' must be a mapping")
        resolved[str(name)] = provider_payload
    return resolved


def _resolve_provider_secrets(
    provider_name: str,
    provider_payload: dict[str, Any],
) -> ProviderSecrets:
    api_base = _resolve_secret_value(
        provider_payload.get("api_base"),
        label=f"providers.{provider_name}.api_base",
    )
    api_key = _resolve_secret_value(
        provider_payload.get("api_key"),
        label=f"providers.{provider_name}.api_key",
    )
    return ProviderSecrets(api_base=api_base, api_key=api_key)


def _load_model_specs(
    models_path: Path,
    secrets_path: Path,
) -> list[ModelSpec]:
    models_payload = _read_yaml(models_path)
    secrets_payload = _read_yaml(secrets_path)
    provider_secrets = _load_provider_secrets(secrets_payload)

    models = models_payload.get("models") or {}
    if not isinstance(models, dict):
        raise ValueError("Expected 'models' to be a mapping in models config")

    specs: list[ModelSpec] = []
    for name, model_payload in models.items():
        if not isinstance(model_payload, dict):
            raise ValueError(f"Model '{name}' must be a mapping")

        raw_type, route_type = _normalize_route_type(model_payload.get("type"))
        provider = model_payload.get("provider")
        litellm_model = model_payload.get("litellm_model")
        supports_tool_calling = model_payload.get("supports_tool_calling")

        if provider is not None and not isinstance(provider, str):
            raise ValueError(f"Model '{name}' has non-string provider")
        if litellm_model is not None and not isinstance(litellm_model, str):
            raise ValueError(f"Model '{name}' has non-string litellm_model")
        if supports_tool_calling is not None and not isinstance(supports_tool_calling, bool):
            raise ValueError(f"Model '{name}' has non-boolean supports_tool_calling")

        skip_reason: str | None = None
        config_error: str | None = None
        api_base: str | None = None
        api_key: str | None = None

        if route_type == "skip":
            skip_reason = f"unsupported type: {raw_type}"
        elif not provider:
            skip_reason = "provider not configured"
        elif not litellm_model:
            skip_reason = "litellm_model not configured"
        else:
            provider_payload = provider_secrets.get(provider)
            if provider_payload is None:
                config_error = f"provider '{provider}' missing in secrets"
            else:
                try:
                    secrets = _resolve_provider_secrets(provider, provider_payload)
                except ValueError as exc:
                    config_error = str(exc)
                else:
                    api_base = secrets.api_base
                    api_key = secrets.api_key
                    if api_key is None:
                        config_error = f"provider '{provider}' api_key not configured"

        specs.append(
            ModelSpec(
                name=str(name),
                raw_type=raw_type,
                route_type=route_type,
                provider=provider,
                litellm_model=litellm_model,
                api_base=api_base,
                api_key=api_key,
                supports_tool_calling=supports_tool_calling,
                skip_reason=skip_reason,
                config_error=config_error,
            )
        )
    return specs


def _select_models(specs: list[ModelSpec], model_names: str) -> list[ModelSpec]:
    if not model_names.strip():
        return specs

    requested = [item.strip() for item in model_names.split(",") if item.strip()]
    seen: set[str] = set()
    spec_map = {spec.name: spec for spec in specs}
    selected: list[ModelSpec] = []
    unknown = [name for name in requested if name not in spec_map]
    if unknown:
        raise typer.BadParameter(
            f"Unknown models: {', '.join(unknown)}",
            param_hint="--models",
        )

    for name in requested:
        if name in seen:
            continue
        selected.append(spec_map[name])
        seen.add(name)
    return selected


def _read_field(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _extract_tool_calls(response: Any) -> list[Any]:
    choices = _read_field(response, "choices") or []
    if not choices:
        return []
    message = _read_field(choices[0], "message")
    if message is None:
        return []

    raw_tool_calls = _read_field(message, "tool_calls")
    if isinstance(raw_tool_calls, list):
        return raw_tool_calls
    if raw_tool_calls:
        return [raw_tool_calls]

    legacy_call = _read_field(message, "function_call")
    if legacy_call:
        return [legacy_call]
    return []


def _truncate_detail(detail: str, *, max_len: int = 60) -> str:
    text = " ".join(detail.split())
    if not text:
        return "—"
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _style_status(status: str) -> str:
    if status == "OK":
        return "[green]✅ OK[/green]"
    if status == "FAIL":
        return "[red]❌ FAIL[/red]"
    return "[yellow]⏭ SKIP[/yellow]"


def _style_tool_call(tool_call: str) -> str:
    if tool_call == "ok":
        return "[green]✅[/green]"
    if tool_call == "fail":
        return "[red]❌[/red]"
    return "—"


async def _probe_chat_model(
    spec: ModelSpec,
    *,
    timeout: float,
    tool_call_enabled: bool,
    probe_tools: list[dict[str, Any]],
    tool_prompt: str,
) -> ProbeResult:
    start = perf_counter()
    effective_probe_tools = probe_tools
    if tool_call_enabled and is_antigravity_provider(spec.provider):
        effective_probe_tools = transform_antigravity_tools(probe_tools).tools
    try:
        with anyio.move_on_after(timeout) as scope:
            if tool_call_enabled:
                response = await acompletion(
                    model=spec.litellm_model,
                    api_base=spec.api_base,
                    api_key=spec.api_key,
                    messages=[{"role": "user", "content": tool_prompt}],
                    tools=effective_probe_tools,
                    tool_choice="auto",
                    max_tokens=64,
                    **_build_request_kwargs(litellm_model=spec.litellm_model),
                )
            else:
                response = await acompletion(
                    model=spec.litellm_model,
                    api_base=spec.api_base,
                    api_key=spec.api_key,
                    messages=PING_MESSAGES,
                    max_tokens=16,
                    **_build_request_kwargs(litellm_model=spec.litellm_model),
                )
    except Exception as exc:
        latency_ms = (perf_counter() - start) * 1000
        detail = str(exc).strip() or exc.__class__.__name__
        return ProbeResult(
            name=spec.name,
            route_type=spec.route_type,
            provider=spec.provider,
            status="FAIL",
            latency_ms=latency_ms,
            tool_call="fail" if tool_call_enabled else "skip",
            detail=detail,
        )

    latency_ms = (perf_counter() - start) * 1000
    if scope.cancelled_caught:
        return ProbeResult(
            name=spec.name,
            route_type=spec.route_type,
            provider=spec.provider,
            status="FAIL",
            latency_ms=latency_ms,
            tool_call="fail" if tool_call_enabled else "skip",
            detail=f"timed out after {timeout:.1f}s",
        )

    tool_call = "skip"
    detail = ""
    if tool_call_enabled:
        tool_calls = _extract_tool_calls(response)
        if tool_calls:
            tool_call = "ok"
            detail = f"tool_calls={len(tool_calls)}"
        else:
            tool_call = "fail"
            detail = "tool_calls=0"

    return ProbeResult(
        name=spec.name,
        route_type=spec.route_type,
        provider=spec.provider,
        status="OK",
        latency_ms=latency_ms,
        tool_call=tool_call,
        detail=detail,
    )


async def _probe_embedding_model(
    spec: ModelSpec,
    *,
    timeout: float,
) -> ProbeResult:
    start = perf_counter()
    try:
        with anyio.move_on_after(timeout) as scope:
            await aembedding(
                model=spec.litellm_model,
                api_base=spec.api_base,
                api_key=spec.api_key,
                input=EMBEDDING_INPUT,
            )
    except Exception as exc:
        latency_ms = (perf_counter() - start) * 1000
        detail = str(exc).strip() or exc.__class__.__name__
        return ProbeResult(
            name=spec.name,
            route_type=spec.route_type,
            provider=spec.provider,
            status="FAIL",
            latency_ms=latency_ms,
            tool_call="skip",
            detail=detail,
        )

    latency_ms = (perf_counter() - start) * 1000
    if scope.cancelled_caught:
        return ProbeResult(
            name=spec.name,
            route_type=spec.route_type,
            provider=spec.provider,
            status="FAIL",
            latency_ms=latency_ms,
            tool_call="skip",
            detail=f"timed out after {timeout:.1f}s",
        )

    return ProbeResult(
        name=spec.name,
        route_type=spec.route_type,
        provider=spec.provider,
        status="OK",
        latency_ms=latency_ms,
        tool_call="skip",
        detail="",
    )


async def _probe_model(
    spec: ModelSpec,
    *,
    timeout: float,
    tool_call_enabled: bool,
    probe_tools: list[dict[str, Any]],
    tool_prompt: str,
) -> ProbeResult:
    if spec.skip_reason:
        return ProbeResult(
            name=spec.name,
            route_type="skip",
            provider=spec.provider,
            status="SKIP",
            latency_ms=None,
            tool_call="skip",
            detail=spec.skip_reason,
        )

    if spec.config_error:
        return ProbeResult(
            name=spec.name,
            route_type=spec.route_type,
            provider=spec.provider,
            status="FAIL",
            latency_ms=None,
            tool_call="fail" if spec.route_type == "chat" and tool_call_enabled else "skip",
            detail=spec.config_error,
        )

    if spec.route_type == "chat":
        return await _probe_chat_model(
            spec,
            timeout=timeout,
            tool_call_enabled=tool_call_enabled,
            probe_tools=probe_tools,
            tool_prompt=tool_prompt,
        )
    if spec.route_type == "embedding":
        return await _probe_embedding_model(spec, timeout=timeout)

    return ProbeResult(
        name=spec.name,
        route_type="skip",
        provider=spec.provider,
        status="SKIP",
        latency_ms=None,
        tool_call="skip",
        detail=f"unsupported type: {spec.raw_type}",
    )


async def _probe_models(
    specs: list[ModelSpec],
    *,
    timeout: float,
    tool_call_enabled: bool,
    concurrency: int,
    probe_tools: list[dict[str, Any]],
    tool_prompt: str,
) -> list[ProbeResult]:
    semaphore = anyio.Semaphore(concurrency)
    results: list[ProbeResult | None] = [None] * len(specs)

    async def worker(index: int, spec: ModelSpec) -> None:
        async with semaphore:
            results[index] = await _probe_model(
                spec,
                timeout=timeout,
                tool_call_enabled=tool_call_enabled,
                probe_tools=probe_tools,
                tool_prompt=tool_prompt,
            )

    async with anyio.create_task_group() as task_group:
        for index, spec in enumerate(specs):
            task_group.start_soon(worker, index, spec)

    return [result for result in results if result is not None]


def _render_results(results: list[ProbeResult], *, elapsed_seconds: float) -> None:
    table = Table(title="Model Check Report", header_style="bold cyan")
    table.add_column(
        "Model Name",
        style="bold",
        no_wrap=True,
        overflow="ellipsis",
        max_width=18,
    )
    table.add_column("Type", no_wrap=True, max_width=9)
    table.add_column(
        "Provider",
        no_wrap=True,
        overflow="ellipsis",
        max_width=18,
    )
    table.add_column("Status", no_wrap=True, max_width=8)
    table.add_column("Latency", no_wrap=True, max_width=12)
    table.add_column("Tool Call", no_wrap=True, max_width=9)
    table.add_column("Detail", overflow="fold", min_width=16)

    for result in results:
        latency = "—" if result.latency_ms is None else f"{result.latency_ms:.1f} ms"
        table.add_row(
            result.name,
            result.route_type,
            result.provider or "—",
            _style_status(result.status),
            latency,
            _style_tool_call(result.tool_call),
            _truncate_detail(result.detail),
        )

    total = len(results)
    ok_count = sum(1 for result in results if result.status == "OK")
    fail_count = sum(1 for result in results if result.status == "FAIL")
    skip_count = sum(1 for result in results if result.status == "SKIP")
    summary = (
        f"Total: {total}\n"
        f"OK: {ok_count}\n"
        f"FAIL: {fail_count}\n"
        f"SKIP: {skip_count}\n"
        f"Total Elapsed: {elapsed_seconds:.2f}s"
    )

    console.print(table)
    console.print(Panel.fit(summary, title="Summary"))


async def _run_checks(
    *,
    models_path: Path,
    secrets_path: Path,
    models: str,
    timeout: float,
    tool_call_enabled: bool,
    concurrency: int,
    minimal: bool,
) -> int:
    specs = _load_model_specs(models_path, secrets_path)
    selected_specs = _select_models(specs, models)
    probe_tools = list(PROBE_TOOLS) if minimal else await _load_full_probe_tools()
    tool_prompt = TOOL_PROMPT if minimal else FULL_TOOL_PROMPT
    started = perf_counter()
    results = await _probe_models(
        selected_specs,
        timeout=timeout,
        tool_call_enabled=tool_call_enabled,
        concurrency=concurrency,
        probe_tools=probe_tools,
        tool_prompt=tool_prompt,
    )
    elapsed_seconds = perf_counter() - started
    _render_results(results, elapsed_seconds=elapsed_seconds)
    return 1 if any(result.status == "FAIL" for result in results) else 0


@app.command()
def main(
    models_path: Annotated[
        Path,
        typer.Option(help="Path to models.yaml"),
    ] = Path("config/models.yaml"),
    secrets_path: Annotated[
        Path,
        typer.Option(help="Path to secrets.yaml"),
    ] = Path("config/secrets.yaml"),
    models: Annotated[
        str,
        typer.Option(help="Comma-separated model names. Empty means all models."),
    ] = "",
    timeout: Annotated[
        float,
        typer.Option(help="Timeout per model in seconds."),
    ] = 15.0,
    tool_call: Annotated[
        bool,
        typer.Option("--tool-call/--no-tool-call", help="Enable tool calling checks for chat models."),
    ] = True,
    concurrency: Annotated[
        int,
        typer.Option(help="Maximum number of concurrent model probes.", min=1),
    ] = 4,
    minimal: Annotated[
        bool,
        typer.Option("--minimal", help="Use a single minimal tool instead of the full ToolRegistry tool set."),
    ] = False,
) -> None:
    try:
        exit_code = anyio.run(
            partial(
                _run_checks,
                models_path=models_path,
                secrets_path=secrets_path,
                models=models,
                timeout=timeout,
                tool_call_enabled=tool_call,
                concurrency=concurrency,
                minimal=minimal,
            ),
            backend="asyncio",
        )
    except Exception as exc:
        console.print(Panel.fit(_truncate_detail(str(exc), max_len=120), title="Error", border_style="red"))
        raise typer.Exit(code=1) from exc

    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
