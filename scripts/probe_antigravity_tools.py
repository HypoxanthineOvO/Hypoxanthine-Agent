#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from time import perf_counter
import time
import urllib.error
import urllib.request
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from hypo_agent.gateway.app import create_app
from hypo_agent.models import Message


ANTHROPIC_VERSION = "2023-06-01"
PROBE_DIR = Path("/tmp/antigravity_probe")
DEFAULT_PROMPT = "请列出当前目录。"
MODEL_CHOICES = ("GeminiLow", "GeminiFlash", "Claude")
MODE_CHOICES = ("raw", "sanitized", "minimal")
EXPERIMENT_CHOICES = ("A", "B", "C", "D", "E")
STRUCTURAL_VARIANTS = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
BLACKLIST_KEYS = {
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "default",
    "$ref",
    "oneOf",
    "anyOf",
    "allOf",
    "not",
}


def _load_auth_token() -> str:
    payload = yaml.safe_load((ROOT_DIR / "config" / "security.yaml").read_text(encoding="utf-8")) or {}
    token = payload.get("auth_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    nested = payload.get("security") or {}
    nested_token = nested.get("auth_token")
    if isinstance(nested_token, str) and nested_token.strip():
        return nested_token.strip()
    raise RuntimeError("auth_token not found in config/security.yaml")


def _append_description(node: dict[str, Any], notes: list[str]) -> None:
    cleaned_notes = [str(item).strip() for item in notes if str(item).strip()]
    if not cleaned_notes:
        return
    existing = str(node.get("description") or "").strip()
    suffix = f" ({'; '.join(cleaned_notes)})"
    node["description"] = f"{existing}{suffix}" if existing else suffix.strip()


def _coerce_json_type(branch: Any) -> str | None:
    if not isinstance(branch, dict):
        return None
    branch_type = branch.get("type")
    return str(branch_type).strip() if isinstance(branch_type, str) and branch_type.strip() else None


def _merge_branch(base: dict[str, Any], branch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in branch.items():
        if key == "description" and key in merged:
            existing = str(merged.get("description") or "").strip()
            incoming = str(value or "").strip()
            if existing and incoming and incoming not in existing:
                merged[key] = f"{existing} {incoming}".strip()
            elif incoming:
                merged[key] = incoming
            continue
        merged[key] = value
    return merged


def _sanitize_schema_node(
    node: Any,
    *,
    field_name: str | None = None,
    parent_required: set[str] | None = None,
) -> Any:
    if isinstance(node, list):
        return [
            _sanitize_schema_node(item, field_name=field_name, parent_required=None)
            for item in node
        ]

    if not isinstance(node, dict):
        return deepcopy(node)

    working = deepcopy(node)
    notes: list[str] = []

    if "anyOf" in working:
        raw_variants = working.pop("anyOf")
        variants = raw_variants if isinstance(raw_variants, list) else []
        non_null_variants: list[dict[str, Any]] = []
        alternate_types: list[str] = []
        saw_null = False
        for variant in variants:
            variant_type = _coerce_json_type(variant)
            if variant_type == "null":
                saw_null = True
                continue
            if isinstance(variant, dict):
                non_null_variants.append(variant)
                if len(non_null_variants) > 1 and variant_type:
                    alternate_types.append(variant_type)
        if saw_null and parent_required is not None and field_name:
            parent_required.discard(field_name)
        if non_null_variants:
            chosen = _sanitize_schema_node(non_null_variants[0], field_name=field_name, parent_required=None)
            if not isinstance(chosen, dict):
                chosen = {"type": "string"}
            if alternate_types:
                notes.append(f"also can be: {', '.join(alternate_types)}")
            working = _merge_branch(working, chosen)
        else:
            working.setdefault("type", "string")
            notes.append("nullable field")

    for compound_key in ("oneOf", "allOf"):
        if compound_key not in working:
            continue
        raw_variants = working.pop(compound_key)
        variants = raw_variants if isinstance(raw_variants, list) else []
        chosen_branch = None
        alternate_types: list[str] = []
        for index, variant in enumerate(variants):
            if not isinstance(variant, dict):
                continue
            variant_type = _coerce_json_type(variant)
            if index == 0:
                chosen_branch = variant
            elif variant_type:
                alternate_types.append(variant_type)
        if chosen_branch is not None:
            chosen = _sanitize_schema_node(chosen_branch, field_name=field_name, parent_required=None)
            if isinstance(chosen, dict):
                working = _merge_branch(working, chosen)
        if alternate_types:
            notes.append(f"{compound_key} collapsed, alternates: {', '.join(alternate_types)}")

    if "not" in working:
        notes.append("not constraint removed")
        working.pop("not", None)
    if "$ref" in working:
        notes.append(f"$ref removed: {working.get('$ref')}")
        working.pop("$ref", None)

    for key, label in (
        ("default", "default"),
        ("minimum", "min"),
        ("maximum", "max"),
        ("minLength", "min length"),
        ("maxLength", "max length"),
        ("pattern", "pattern"),
        ("format", "format"),
        ("minItems", "min items"),
        ("maxItems", "max items"),
    ):
        if key not in working:
            continue
        notes.append(f"{label}: {working.pop(key)}")

    required_names = working.get("required")
    required_set = (
        {
            str(item).strip()
            for item in required_names
            if isinstance(required_names, list) and str(item).strip()
        }
        if isinstance(required_names, list)
        else set()
    )

    properties = working.get("properties")
    if isinstance(properties, dict):
        sanitized_props: dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            sanitized_props[prop_name] = _sanitize_schema_node(
                prop_schema,
                field_name=str(prop_name),
                parent_required=required_set,
            )
        working["properties"] = sanitized_props
        if required_set and isinstance(required_names, list):
            working["required"] = [item for item in required_names if isinstance(item, str) and item in required_set]
        elif "required" in working:
            working.pop("required", None)

    items = working.get("items")
    if items is not None:
        working["items"] = _sanitize_schema_node(items, field_name=None, parent_required=None)

    pattern_properties = working.get("patternProperties")
    if isinstance(pattern_properties, dict):
        sanitized_pattern_properties: dict[str, Any] = {}
        for pattern_name, prop_schema in pattern_properties.items():
            sanitized_pattern_properties[pattern_name] = _sanitize_schema_node(
                prop_schema,
                field_name=str(pattern_name),
                parent_required=None,
            )
        if sanitized_pattern_properties:
            working["patternProperties"] = sanitized_pattern_properties
        else:
            working.pop("patternProperties", None)

    additional_properties = working.get("additionalProperties")
    if isinstance(additional_properties, dict):
        working["additionalProperties"] = _sanitize_schema_node(
            additional_properties,
            field_name=None,
            parent_required=None,
        )

    _append_description(working, notes)
    return working


def _build_minimal_tool() -> list[dict[str, Any]]:
    return [
        {
            "name": "echo",
            "description": "Echo back the input text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        }
    ]


def _tool_to_anthropic_payload(tool: dict[str, Any], *, sanitize: bool) -> dict[str, Any]:
    function = tool.get("function") if isinstance(tool, dict) else None
    if not isinstance(function, dict):
        raise ValueError(f"Invalid tool payload: {tool!r}")
    schema = deepcopy(function.get("parameters") or {"type": "object", "properties": {}})
    if sanitize:
        schema = _sanitize_schema_node(schema)
    return {
        "name": str(function.get("name") or "").strip(),
        "description": str(function.get("description") or "").strip(),
        "input_schema": schema,
    }


def _normalize_model_slug(litellm_model: str | None) -> str:
    raw = str(litellm_model or "").strip()
    if "/" not in raw:
        return raw
    return raw.split("/", 1)[1].strip()


def _content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    blocks.append({"type": "text", "text": text})
                continue
            if isinstance(item, dict) and item.get("type") == "input_text":
                text = str(item.get("text") or "").strip()
                if text:
                    blocks.append({"type": "text", "text": text})
                continue
            if isinstance(item, dict) and item.get("type") == "image_url":
                image_url = item.get("image_url") or {}
                url = str(image_url.get("url") or "").strip()
                if url:
                    blocks.append({"type": "text", "text": f"[image] {url}"})
                continue
            blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
        return blocks or [{"type": "text", "text": ""}]
    return [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}]


def _build_request_body(
    *,
    model_slug: str,
    llm_messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    system_blocks: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []

    for message in llm_messages:
        role = str(message.get("role") or "").strip()
        content = message.get("content")
        if role == "system":
            for block in _content_to_anthropic_blocks(content):
                system_blocks.append(block)
            continue
        if role not in {"user", "assistant"}:
            continue
        messages.append(
            {
                "role": role,
                "content": _content_to_anthropic_blocks(content),
            }
        )

    body: dict[str, Any] = {
        "model": model_slug,
        "messages": messages,
        "tools": tools_payload,
        "system": system_blocks,
        "max_tokens": 4096,
    }
    return body


async def _build_probe_payload(
    pipeline: Any,
    *,
    model_alias: str,
    prompt_text: str,
    mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inbound = Message(
        text=prompt_text,
        sender="user",
        session_id=f"probe-{model_alias.lower()}-{mode}",
        channel="webui",
    )
    candidate_skills = pipeline._match_skill_candidates(inbound)
    preloaded_skill_names = (
        pipeline._match_preloaded_skill_names(inbound, candidate_skills=candidate_skills)
        if pipeline._supports_progressive_tool_disclosure()
        else set()
    )
    raw_tools, _ = pipeline._build_exposed_tools(
        inbound=inbound,
        session_id=inbound.session_id,
        preloaded_skill_names=preloaded_skill_names,
    )
    task_type = pipeline._resolve_task_type_for_inbound(
        inbound,
        use_tools=True,
        candidate_skills=candidate_skills,
    )
    llm_messages = await pipeline._build_llm_messages(
        inbound,
        use_tools=True,
        candidate_skills=candidate_skills,
        model_name=model_alias,
        task_type=task_type,
    )

    if mode == "minimal":
        tools_payload = _build_minimal_tool()
    else:
        tools_payload = [
            _tool_to_anthropic_payload(tool, sanitize=(mode == "sanitized"))
            for tool in raw_tools
        ]

    cfg = pipeline.router.config.models[model_alias]
    request_body = _build_request_body(
        model_slug=_normalize_model_slug(cfg.litellm_model),
        llm_messages=llm_messages,
        tools_payload=tools_payload,
    )
    return request_body, {
        "api_base": cfg.api_base,
        "api_key": cfg.api_key,
        "task_type": task_type,
        "tool_count": len(tools_payload),
    }


def _post_anthropic(
    *,
    api_base: str,
    api_key: str,
    request_body: dict[str, Any],
) -> tuple[int | None, Any, float]:
    url = f"{str(api_base).rstrip('/')}/v1/messages"
    payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": api_key,
            "user-agent": "antigravity-probe/1.0",
        },
    )
    started_at = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            raw_body = response.read().decode("utf-8")
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed = raw_body
            return response.status, parsed, elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = (perf_counter() - started_at) * 1000.0
        raw_body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            parsed = raw_body
        return exc.code, parsed, elapsed_ms


def _response_summary(status_code: int | None, response_body: Any) -> str:
    if status_code is None:
        return "no_status"
    if 200 <= status_code < 300:
        return "ok"
    if isinstance(response_body, dict):
        error = response_body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            error_type = str(error.get("type") or "").strip()
            return f"{error_type or 'error'}: {message}".strip(": ")
    return str(response_body)[:160]


def _request_body_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _tool_payload_bytes(tool: dict[str, Any]) -> int:
    return len(json.dumps(tool, ensure_ascii=False).encode("utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_summary(path: Path, rows: list[dict[str, Any]], *, models: list[str], modes: list[str]) -> None:
    row_lookup = {
        (row["model"], row["mode"]): row
        for row in rows
    }
    lines = [
        "# Antigravity Probe Summary",
        "",
        "| model × mode | raw | sanitized | minimal |",
        "| --- | --- | --- | --- |",
    ]
    for model in models:
        cells = []
        for mode in ("raw", "sanitized", "minimal"):
            if mode not in modes:
                cells.append("—")
                continue
            row = row_lookup[(model, mode)]
            status_code = row["status_code"]
            summary = row["summary"].replace("|", "\\|")
            cells.append(f"`{status_code}` {summary}")
        lines.append(f"| {model} | {' | '.join(cells)} |")

    raw_ok = all(
        not (200 <= int(row["status_code"] or 0) < 300)
        for row in rows
        if row["mode"] == "raw"
    )
    sanitized_ok = all(
        200 <= int(row["status_code"] or 0) < 300
        for row in rows
        if row["mode"] == "sanitized"
    )
    minimal_ok = all(
        200 <= int(row["status_code"] or 0) < 300
        for row in rows
        if row["mode"] == "minimal"
    )

    if raw_ok and sanitized_ok and minimal_ok:
        verdict = "假设成立"
    elif sanitized_ok and minimal_ok:
        verdict = "假设部分成立"
    else:
        verdict = "假设不成立（有其他因素）"

    lines.extend(
        [
            "",
            "## 结论",
            f"- {verdict}",
        ]
    )

    unexpected = [
        row
        for row in rows
        if (
            (row["mode"] == "raw" and 200 <= int(row["status_code"] or 0) < 300)
            or (row["mode"] in {"sanitized", "minimal"} and not (200 <= int(row["status_code"] or 0) < 300))
        )
    ]
    if unexpected:
        lines.append("- 意外结果：")
        for row in unexpected:
            lines.append(
                f"  - {row['model']} / {row['mode']}: status={row['status_code']} summary={row['summary']}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    auth_token = _load_auth_token()
    app = create_app(auth_token=auth_token)
    pipeline = app.state.pipeline

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")

    rows: list[dict[str, Any]] = []

    for model_alias in args.model:
        for mode in args.mode:
            request_body, meta = await _build_probe_payload(
                pipeline,
                model_alias=model_alias,
                prompt_text=args.prompt_text,
                mode=mode,
            )
            status_code, response_body, elapsed_ms = _post_anthropic(
                api_base=str(meta["api_base"] or "").strip(),
                api_key=str(meta["api_key"] or "").strip(),
                request_body=request_body,
            )
            record = {
                "timestamp": timestamp,
                "model": model_alias,
                "mode": mode,
                "task_type": meta["task_type"],
                "tool_count": meta["tool_count"],
                "request": request_body,
                "status_code": status_code,
                "response": response_body,
                "elapsed_ms": elapsed_ms,
            }
            output_path = PROBE_DIR / f"{timestamp}_{model_alias}_{mode}.json"
            _write_json(output_path, record)
            rows.append(
                {
                    "model": model_alias,
                    "mode": mode,
                    "status_code": status_code,
                    "summary": _response_summary(status_code, response_body),
                    "path": str(output_path),
                }
            )

    summary_path = Path("/tmp/antigravity_probe_summary.md")
    _write_summary(summary_path, rows, models=args.model, modes=args.mode)
    print(json.dumps({"rows": rows, "summary_path": str(summary_path)}, ensure_ascii=False, indent=2))
    return 0


async def _build_pipeline_context(
    pipeline: Any,
    *,
    model_alias: str,
    prompt_text: str,
    session_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    inbound = Message(
        text=prompt_text,
        sender="user",
        session_id=session_id,
        channel="webui",
    )
    candidate_skills = pipeline._match_skill_candidates(inbound)
    task_type = pipeline._resolve_task_type_for_inbound(
        inbound,
        use_tools=True,
        candidate_skills=candidate_skills,
    )
    llm_messages = await pipeline._build_llm_messages(
        inbound,
        use_tools=True,
        candidate_skills=candidate_skills,
        model_name=model_alias,
        task_type=task_type,
    )
    cfg = pipeline.router.config.models[model_alias]
    return llm_messages, {
        "api_base": cfg.api_base,
        "api_key": cfg.api_key,
        "model_slug": _normalize_model_slug(cfg.litellm_model),
        "task_type": task_type,
    }


def _build_dummy_tool(
    *,
    name: str,
    description: str = "noop",
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": deepcopy(schema or {"type": "object", "properties": {}}),
    }


def _build_dummy_tool_for_target_tool_bytes(
    *,
    target_bytes: int,
    filler: str,
    name: str = "dummy_tool",
) -> dict[str, Any]:
    base_schema = {"type": "object", "properties": {}}
    low = 0
    high = max(8, target_bytes * 2)
    best_tool = _build_dummy_tool(name=name, description="", schema=base_schema)
    best_distance = abs(_tool_payload_bytes(best_tool) - target_bytes)

    while low <= high:
        mid = (low + high) // 2
        description = filler * mid
        tool = _build_dummy_tool(name=name, description=description, schema=base_schema)
        size = _tool_payload_bytes(tool)
        distance = abs(size - target_bytes)
        if distance < best_distance:
            best_tool = tool
            best_distance = distance
        if size < target_bytes:
            low = mid + 1
        elif size > target_bytes:
            high = mid - 1
        else:
            return tool
    return best_tool


def _build_single_tool_request_for_target_body_bytes(
    *,
    base_messages: list[dict[str, Any]],
    model_slug: str,
    target_body_bytes: int,
    filler: str,
    tool_name: str,
) -> dict[str, Any]:
    low = 0
    high = max(8, target_body_bytes * 2)
    best_body = _build_request_body(
        model_slug=model_slug,
        llm_messages=base_messages,
        tools_payload=[_build_dummy_tool(name=tool_name)],
    )
    best_distance = abs(_request_body_bytes(best_body) - target_body_bytes)
    while low <= high:
        mid = (low + high) // 2
        tool = _build_dummy_tool(name=tool_name, description=filler * mid)
        body = _build_request_body(
            model_slug=model_slug,
            llm_messages=base_messages,
            tools_payload=[tool],
        )
        size = _request_body_bytes(body)
        distance = abs(size - target_body_bytes)
        if distance < best_distance:
            best_body = body
            best_distance = distance
        if size < target_body_bytes:
            low = mid + 1
        elif size > target_body_bytes:
            high = mid - 1
        else:
            return body
    return best_body


def _append_system_padding(llm_messages: list[dict[str, Any]], filler: str, count: int) -> list[dict[str, Any]]:
    padded = deepcopy(llm_messages)
    padding = filler * count
    if not padding:
        return padded
    padded.append({"role": "system", "content": padding})
    return padded


def _save_experiment_record(
    *,
    experiment: str,
    param: str,
    model_alias: str,
    request_body: dict[str, Any],
    status_code: int | None,
    response_body: Any,
    elapsed_ms: float,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "experiment": experiment,
        "param": param,
        "model": model_alias,
        "request_body_bytes": _request_body_bytes(request_body),
        "request": request_body,
        "status_code": status_code,
        "response": response_body,
        "elapsed_ms": elapsed_ms,
    }
    if extra:
        payload["extra"] = extra
    path = PROBE_DIR / f"step25_{experiment}_{param}.json"
    _write_json(path, payload)
    return path


def _experiment_result_row(
    *,
    experiment: str,
    param: str,
    path: Path,
    status_code: int | None,
    response_body: Any,
    request_body: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "experiment": experiment,
        "param": param,
        "path": str(path),
        "status_code": status_code,
        "summary": _response_summary(status_code, response_body),
        "request_body_bytes": _request_body_bytes(request_body),
        "extra": extra or {},
    }


def _sleep_between_requests() -> None:
    time.sleep(1)


def _build_real_sanitized_tools(
    raw_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_tool_to_anthropic_payload(tool, sanitize=True) for tool in raw_tools]


def _base8_real_sanitized_tools(
    raw_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sanitized = _build_real_sanitized_tools(raw_tools)
    return sanitized[:8]


def _structural_variant_tool(variant: str) -> dict[str, Any]:
    description = "synthetic structural probe"
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    if variant == "S0":
        return _build_dummy_tool(name="structural_probe", description=description, schema=schema)

    properties: dict[str, Any] = {
        "field1": {"type": "string"},
        "field2": {"type": "string"},
    }
    schema = {"type": "object", "properties": properties}

    if variant in {"S2", "S3", "S4", "S5", "S6"}:
        schema["required"] = ["field1"]

    if variant in {"S3", "S4", "S5", "S6"}:
        properties["tags"] = {
            "type": "array",
            "items": {"type": "string"},
        }

    if variant in {"S4", "S5", "S6"}:
        properties["mode"] = {
            "type": "string",
            "enum": ["a", "b", "c"],
        }

    if variant in {"S5", "S6"}:
        properties["nested"] = {
            "type": "object",
            "properties": {
                "child": {"type": "string"},
                "flag": {"type": "boolean"},
            },
            "required": ["child"],
        }

    if variant == "S6":
        description = (
            "synthetic structural probe with real-tool-sized description. "
            + ("用于探测 Antigravity 对复杂 tool schema 的组合限制。" * 18)
        )

    return _build_dummy_tool(name="structural_probe", description=description, schema=schema)


def _structural_variant_extra(variant: str) -> dict[str, Any]:
    tool = _structural_variant_tool(variant)
    schema = tool["input_schema"]

    def _count_nodes(node: Any) -> int:
        if isinstance(node, dict):
            return 1 + sum(_count_nodes(value) for value in node.values())
        if isinstance(node, list):
            return 1 + sum(_count_nodes(item) for item in node)
        return 1

    return {
        "tool_description_len": len(tool["description"]),
        "tool_schema_nodes": _count_nodes(schema),
        "tool_bytes": _tool_payload_bytes(tool),
        "schema": schema,
    }


def _minimalized_real_tool_payload(name: str) -> dict[str, Any]:
    if name == "web_search":
        return _build_dummy_tool(
            name="web_search",
            description="Search the web and return ranked results for a query.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        )
    raise ValueError(f"Unsupported minimalized real tool: {name}")


async def _run_experiment_a(
    *,
    llm_messages: list[dict[str, Any]],
    meta: dict[str, Any],
    model_alias: str,
) -> list[dict[str, Any]]:
    counts = [8, 9, 10, 15, 20, 30, 50]
    rows: list[dict[str, Any]] = []
    for count in counts:
        tools = [
            _build_dummy_tool(name=f"dummy_{index:02d}")
            for index in range(1, count + 1)
        ]
        request_body = _build_request_body(
            model_slug=meta["model_slug"],
            llm_messages=llm_messages,
            tools_payload=tools,
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        path = _save_experiment_record(
            experiment="A",
            param=f"n{count}",
            model_alias=model_alias,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
            extra={"tool_count": count},
        )
        rows.append(
            _experiment_result_row(
                experiment="A",
                param=f"n{count}",
                path=path,
                status_code=status_code,
                response_body=response_body,
                request_body=request_body,
                extra={"tool_count": count},
            )
        )
        _sleep_between_requests()
    return rows


async def _run_experiment_b(
    *,
    llm_messages: list[dict[str, Any]],
    meta: dict[str, Any],
    model_alias: str,
) -> list[dict[str, Any]]:
    size_kb_values = [1, 5, 10, 15, 20, 30]
    rows: list[dict[str, Any]] = []
    for size_kb in size_kb_values:
        tool = _build_dummy_tool_for_target_tool_bytes(
            target_bytes=size_kb * 1024,
            filler="A",
            name=f"dummy_big_{size_kb}kb",
        )
        request_body = _build_request_body(
            model_slug=meta["model_slug"],
            llm_messages=llm_messages,
            tools_payload=[tool],
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        path = _save_experiment_record(
            experiment="B",
            param=f"{size_kb}kb",
            model_alias=model_alias,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
            extra={"tool_bytes": _tool_payload_bytes(tool)},
        )
        rows.append(
            _experiment_result_row(
                experiment="B",
                param=f"{size_kb}kb",
                path=path,
                status_code=status_code,
                response_body=response_body,
                request_body=request_body,
                extra={"tool_bytes": _tool_payload_bytes(tool)},
            )
        )
        _sleep_between_requests()
    return rows


async def _run_experiment_c(
    *,
    llm_messages: list[dict[str, Any]],
    meta: dict[str, Any],
    model_alias: str,
) -> list[dict[str, Any]]:
    target_body_bytes = 14 * 1024
    variants = [
        ("ascii14kb", "A"),
        ("unicode14kb", "测"),
    ]
    rows: list[dict[str, Any]] = []
    for param, filler in variants:
        request_body = _build_single_tool_request_for_target_body_bytes(
            base_messages=llm_messages,
            model_slug=meta["model_slug"],
            target_body_bytes=target_body_bytes,
            filler=filler,
            tool_name=f"dummy_{param}",
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        path = _save_experiment_record(
            experiment="C",
            param=param,
            model_alias=model_alias,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
        )
        rows.append(
            _experiment_result_row(
                experiment="C",
                param=param,
                path=path,
                status_code=status_code,
                response_body=response_body,
                request_body=request_body,
            )
        )
        _sleep_between_requests()
    return rows


async def _run_experiment_d(
    *,
    llm_messages: list[dict[str, Any]],
    meta: dict[str, Any],
    model_alias: str,
) -> list[dict[str, Any]]:
    targets = [13050, 13100, 13200, 13250, 13300, 13340]
    rows: list[dict[str, Any]] = []
    for target in targets:
        request_body = _build_single_tool_request_for_target_body_bytes(
            base_messages=llm_messages,
            model_slug=meta["model_slug"],
            target_body_bytes=target,
            filler="A",
            tool_name=f"dummy_d_{target}",
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        path = _save_experiment_record(
            experiment="D",
            param=str(target),
            model_alias=model_alias,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
        )
        rows.append(
            _experiment_result_row(
                experiment="D",
                param=str(target),
                path=path,
                status_code=status_code,
                response_body=response_body,
                request_body=request_body,
            )
        )
        _sleep_between_requests()
    return rows


async def _run_experiment_e(
    *,
    llm_messages: list[dict[str, Any]],
    meta: dict[str, Any],
    model_alias: str,
) -> list[dict[str, Any]]:
    padded_messages = _append_system_padding(llm_messages, "A", 10 * 1024)
    request_body = _build_request_body(
        model_slug=meta["model_slug"],
        llm_messages=padded_messages,
        tools_payload=_build_minimal_tool(),
    )
    status_code, response_body, elapsed_ms = _post_anthropic(
        api_base=str(meta["api_base"] or "").strip(),
        api_key=str(meta["api_key"] or "").strip(),
        request_body=request_body,
    )
    path = _save_experiment_record(
        experiment="E",
        param="system10kb",
        model_alias=model_alias,
        request_body=request_body,
        status_code=status_code,
        response_body=response_body,
        elapsed_ms=elapsed_ms,
    )
    _sleep_between_requests()
    return [
        _experiment_result_row(
            experiment="E",
            param="system10kb",
            path=path,
            status_code=status_code,
            response_body=response_body,
            request_body=request_body,
        )
    ]


def _write_step25_report(
    path: Path,
    *,
    experiment_rows: dict[str, list[dict[str, Any]]],
    skipped: list[str],
) -> None:
    lines = ["# Antigravity Step 2.5 Report", ""]
    for experiment in ("A", "B", "C", "D", "E"):
        rows = experiment_rows.get(experiment, [])
        lines.append(f"## 实验 {experiment}")
        if rows:
            lines.append("")
            lines.append("| param | status | summary | body bytes |")
            lines.append("| --- | --- | --- | --- |")
            for row in rows:
                lines.append(
                    f"| {row['param']} | `{row['status_code']}` | {row['summary']} | {row['request_body_bytes']} |"
                )
        else:
            lines.append("")
            lines.append("- skipped")
        lines.append("")

    a_rows = experiment_rows.get("A", [])
    b_rows = experiment_rows.get("B", [])
    conclusion = "🔴 其他（具体描述）"
    strategy = "需先确认多 tool / payload 组合限制，再决定 provider 出口策略。"

    if a_rows:
        first_failure = next((row for row in a_rows if int(row["status_code"] or 0) >= 400), None)
        if first_failure and first_failure["param"] == "n9":
            thirty_kb_ok = any(
                row["param"] == "30kb" and 200 <= int(row["status_code"] or 0) < 300
                for row in b_rows
            )
            if thirty_kb_ok:
                conclusion = "🔴 硬 tool count 限制（N ≈ 8）"
                strategy = "Antigravity provider 需要 tool 裁剪 / progressive disclosure；schema 清洗只能作为附加修复。"

    lines.extend(
        [
            "## 判读结论",
            f"- {conclusion}",
            "",
            "## Step 3 策略推荐",
            f"- {strategy}",
        ]
    )
    if skipped:
        lines.extend(["", "## 跳过说明"])
        for item in skipped:
            lines.append(f"- {item}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run_experiments(args: argparse.Namespace) -> int:
    auth_token = _load_auth_token()
    app = create_app(auth_token=auth_token)
    pipeline = app.state.pipeline
    PROBE_DIR.mkdir(parents=True, exist_ok=True)

    llm_messages, meta = await _build_pipeline_context(
        pipeline,
        model_alias=args.model,
        prompt_text=args.prompt_text,
        session_id="step25-base",
    )

    experiment_rows: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    requested = list(dict.fromkeys(args.experiment))

    for experiment in requested:
        if experiment == "A":
            experiment_rows["A"] = await _run_experiment_a(
                llm_messages=llm_messages,
                meta=meta,
                model_alias=args.model,
            )
            continue
        if experiment == "B":
            experiment_rows["B"] = await _run_experiment_b(
                llm_messages=llm_messages,
                meta=meta,
                model_alias=args.model,
            )
            continue
        if experiment == "C":
            a_rows = experiment_rows.get("A", [])
            b_rows = experiment_rows.get("B", [])
            count_limit_indicated = any(row["param"] == "n9" and int(row["status_code"] or 0) >= 400 for row in a_rows)
            large_single_ok = any(row["param"] == "30kb" and 200 <= int(row["status_code"] or 0) < 300 for row in b_rows)
            if count_limit_indicated and large_single_ok:
                skipped.append("实验 C 跳过：A 显示 9 个极简 tools 即失败，B 显示单个 30KB tool 仍成功，已足够排除字节/token 主导上限。")
                continue
            experiment_rows["C"] = await _run_experiment_c(
                llm_messages=llm_messages,
                meta=meta,
                model_alias=args.model,
            )
            continue
        if experiment == "D":
            skipped.append("实验 D 跳过：当前结果不指向字节硬上限，而指向 tool count 限制。")
            continue
        if experiment == "E":
            a_rows = experiment_rows.get("A", [])
            b_rows = experiment_rows.get("B", [])
            count_limit_indicated = any(row["param"] == "n9" and int(row["status_code"] or 0) >= 400 for row in a_rows)
            large_single_ok = any(row["param"] == "30kb" and 200 <= int(row["status_code"] or 0) < 300 for row in b_rows)
            if count_limit_indicated and large_single_ok:
                skipped.append("实验 E 跳过：单 tool 大 payload 成功，问题不在 system+tool 联合字节预算。")
                continue
            experiment_rows["E"] = await _run_experiment_e(
                llm_messages=llm_messages,
                meta=meta,
                model_alias=args.model,
            )
            continue

    report_path = Path("/tmp/antigravity_step25_report.md")
    _write_step25_report(report_path, experiment_rows=experiment_rows, skipped=skipped)
    print(json.dumps({"report_path": str(report_path), "experiments": experiment_rows, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


def _write_step25b_report(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    node_budget_row: dict[str, Any] | None,
    trigger_variant: str | None,
) -> None:
    row_by_param = {row["param"]: row for row in rows}
    lines = [
        "# Antigravity Step 2.5b Structural Report",
        "",
        "## S0-S6",
        "",
        "| variant | status | summary | body bytes | synthetic tool bytes | schema nodes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for variant in STRUCTURAL_VARIANTS:
        row = row_by_param[variant]
        extra = row.get("extra", {})
        lines.append(
            f"| {variant} | `{row['status_code']}` | {row['summary']} | {row['request_body_bytes']} | {extra.get('tool_bytes','')} | {extra.get('tool_schema_nodes','')} |"
        )

    lines.extend(["", "## 节点预算假设验证", ""])
    if node_budget_row is None:
        lines.append("- skipped")
    else:
        extra = node_budget_row.get("extra", {})
        lines.extend(
            [
                "| param | status | summary | body bytes | synthetic tool bytes |",
                "| --- | --- | --- | --- | --- |",
                f"| {node_budget_row['param']} | `{node_budget_row['status_code']}` | {node_budget_row['summary']} | {node_budget_row['request_body_bytes']} | {extra.get('tool_bytes','')} |",
            ]
        )

    lines.extend(["", "## 定性结论"])
    if trigger_variant is None:
        lines.append("- S0-S6 全部通过，单个第 9 个 synthetic tool 的结构复杂度不足以触发问题。")
    else:
        trigger = row_by_param[trigger_variant]
        lines.append(f"- 第一个从“过”变“挂”的结构变体：`{trigger_variant}`")
        lines.append(
            f"- 触发点摘要：status=`{trigger['status_code']}` summary=`{trigger['summary']}` body_bytes={trigger['request_body_bytes']}"
        )

    if node_budget_row is not None:
        lines.append(
            f"- 节点预算假设验证：`{node_budget_row['param']}` -> status=`{node_budget_row['status_code']}` summary=`{node_budget_row['summary']}`"
        )

    lines.extend(
        [
            "",
            "## Step 3 精准目标",
        ]
    )
    if trigger_variant is None and node_budget_row is not None and int(node_budget_row["status_code"] or 0) >= 400:
        lines.append("- 问题更像是跨 tool 的真实 schema 复杂度累计，而不是第 9 个 tool 自身的单一结构特征。")
        lines.append("- Step 3 应优先做：真实 tool projection / minification + progressive disclosure，而不是只砍某一个 schema 关键字。")
    elif trigger_variant is None:
        lines.append("- synthetic 结构仍未复现失败，说明真实 tool 的语义组合/字段分布本身才是触发器。")
        lines.append("- Step 3 应优先做：基于真实 tool 的结构压缩和按需暴露，不建议先做激进扁平化。")
    else:
        lines.append(f"- Step 3 应优先围绕 `{trigger_variant}` 首次引入的结构特征做精准裁剪。")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run_structural(args: argparse.Namespace) -> int:
    auth_token = _load_auth_token()
    app = create_app(auth_token=auth_token)
    pipeline = app.state.pipeline
    PROBE_DIR.mkdir(parents=True, exist_ok=True)

    session_id = "step25b-base"
    inbound = Message(
        text=args.prompt_text,
        sender="user",
        session_id=session_id,
        channel="webui",
    )
    candidate_skills = pipeline._match_skill_candidates(inbound)
    preloaded_skill_names = (
        pipeline._match_preloaded_skill_names(inbound, candidate_skills=candidate_skills)
        if pipeline._supports_progressive_tool_disclosure()
        else set()
    )
    raw_tools, _ = pipeline._build_exposed_tools(
        inbound=inbound,
        session_id=session_id,
        preloaded_skill_names=preloaded_skill_names,
    )
    llm_messages, meta = await _build_pipeline_context(
        pipeline,
        model_alias=args.model,
        prompt_text=args.prompt_text,
        session_id=session_id,
    )

    base8_tools = _base8_real_sanitized_tools(raw_tools)
    rows: list[dict[str, Any]] = []
    trigger_variant: str | None = None

    for variant in STRUCTURAL_VARIANTS:
        synthetic_tool = _structural_variant_tool(variant)
        request_body = _build_request_body(
            model_slug=meta["model_slug"],
            llm_messages=llm_messages,
            tools_payload=[*base8_tools, synthetic_tool],
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        extra = _structural_variant_extra(variant)
        path = _save_experiment_record(
            experiment="step25b",
            param=variant,
            model_alias=args.model,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
            extra=extra,
        )
        row = _experiment_result_row(
            experiment="step25b",
            param=variant,
            path=path,
            status_code=status_code,
            response_body=response_body,
            request_body=request_body,
            extra=extra,
        )
        rows.append(row)
        if trigger_variant is None and int(status_code or 0) >= 400:
            trigger_variant = variant
        _sleep_between_requests()

    node_budget_row: dict[str, Any] | None = None
    if trigger_variant is None:
        request_body = _build_request_body(
            model_slug=meta["model_slug"],
            llm_messages=llm_messages,
            tools_payload=[*base8_tools, _minimalized_real_tool_payload("web_search")],
        )
        status_code, response_body, elapsed_ms = _post_anthropic(
            api_base=str(meta["api_base"] or "").strip(),
            api_key=str(meta["api_key"] or "").strip(),
            request_body=request_body,
        )
        extra = {
            "tool_bytes": _tool_payload_bytes(_minimalized_real_tool_payload("web_search")),
        }
        path = _save_experiment_record(
            experiment="step25b",
            param="real_min_web_search",
            model_alias=args.model,
            request_body=request_body,
            status_code=status_code,
            response_body=response_body,
            elapsed_ms=elapsed_ms,
            extra=extra,
        )
        node_budget_row = _experiment_result_row(
            experiment="step25b",
            param="real_min_web_search",
            path=path,
            status_code=status_code,
            response_body=response_body,
            request_body=request_body,
            extra=extra,
        )
        _sleep_between_requests()

    report_path = Path("/tmp/antigravity_step25b_structural.md")
    _write_step25b_report(
        report_path,
        rows=rows,
        node_budget_row=node_budget_row,
        trigger_variant=trigger_variant,
    )
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "rows": rows,
                "node_budget_row": node_budget_row,
                "trigger_variant": trigger_variant,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Antigravity Anthropic-compat behavior.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix_parser = subparsers.add_parser("matrix", help="Run the Step 2 raw/sanitized/minimal matrix.")
    matrix_parser.add_argument(
        "--model",
        action="append",
        choices=MODEL_CHOICES,
        required=True,
        help="Configured model alias to probe. Repeat for multiple models.",
    )
    matrix_parser.add_argument(
        "--mode",
        action="append",
        choices=MODE_CHOICES,
        required=True,
        help="Probe mode. Repeat for multiple modes.",
    )
    matrix_parser.add_argument(
        "--prompt-text",
        default=DEFAULT_PROMPT,
        help="Prompt text used to build the real pipeline request body.",
    )
    matrix_parser.add_argument(
        "--timestamp",
        default=None,
        help="Optional timestamp prefix for output files.",
    )

    experiment_parser = subparsers.add_parser("experiment", help="Run Step 2.5 limit characterization experiments.")
    experiment_parser.add_argument(
        "--experiment",
        action="append",
        choices=EXPERIMENT_CHOICES,
        required=True,
        help="Experiment to run. Repeat to request multiple experiments in order.",
    )
    experiment_parser.add_argument(
        "--model",
        choices=MODEL_CHOICES,
        default="GeminiLow",
        help="Model alias to probe for experiments. Step 2.5 defaults to GeminiLow.",
    )
    experiment_parser.add_argument(
        "--prompt-text",
        default=DEFAULT_PROMPT,
        help="Prompt text used to build the real pipeline request body.",
    )

    structural_parser = subparsers.add_parser("structural", help="Run Step 2.5b structural calibration.")
    structural_parser.add_argument(
        "--model",
        choices=MODEL_CHOICES,
        default="GeminiLow",
        help="Model alias to probe for structural calibration.",
    )
    structural_parser.add_argument(
        "--prompt-text",
        default=DEFAULT_PROMPT,
        help="Prompt text used to build the real pipeline request body.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "matrix":
        deduped_models = list(dict.fromkeys(args.model))
        deduped_modes = list(dict.fromkeys(args.mode))
        args.model = deduped_models
        args.mode = deduped_modes
        return asyncio.run(_run(args))
    if args.command == "structural":
        return asyncio.run(_run_structural(args))
    requested = list(dict.fromkeys(args.experiment))
    args.experiment = requested
    return asyncio.run(_run_experiments(args))


if __name__ == "__main__":
    raise SystemExit(main())
