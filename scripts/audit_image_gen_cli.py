"""Audit gpt_image_cli capabilities for C2 M0 contract design.

Probes the gpt_image_cli CLI wrapper to discover all available subcommands,
parameters, and configuration state. Returns a structured report for
architecture documentation.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any


def _finding(finding_id: str, severity: str, summary: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
    }


def _dimension(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "findings": findings,
        "finding_count": len(findings),
        "critical_count": sum(1 for item in findings if item["severity"] == "Critical"),
        "warning_count": sum(1 for item in findings if item["severity"] == "Warning"),
        "info_count": sum(1 for item in findings if item["severity"] == "Info"),
    }


def _cli_available() -> bool:
    return shutil.which("gpt_image_cli") is not None


def _run_help(args: list[str] | None = None) -> tuple[int, str, str]:
    """Run gpt_image_cli with given args and return (returncode, stdout, stderr)."""
    cmd = ["gpt_image_cli"] + (args or [])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return -1, "", str(exc)


def _parse_help_params(help_text: str) -> list[dict[str, str]]:
    """Parse argparse help output into parameter descriptors."""
    params: list[dict[str, str]] = []
    # Match lines like:  --param-name PARAM_NAME  description
    pattern = re.compile(r"^\s+(--[\w-]+)(?:\s+([\w-]+))?\s*(.*)$")
    for line in help_text.splitlines():
        m = pattern.match(line)
        if m:
            name = m.group(1)
            metavar = m.group(2) or ""
            desc = m.group(3).strip()
            params.append({"name": name, "metavar": metavar, "description": desc})
    return params


def _check_env_config() -> dict[str, Any]:
    """Check ~/.config/gpt_image_cli/env existence and permissions."""
    env_path = Path.home() / ".config" / "gpt_image_cli" / "env"
    result: dict[str, Any] = {"path": str(env_path), "exists": env_path.exists()}
    if env_path.exists():
        mode = oct(stat.S_IMODE(env_path.stat().st_mode))
        result["mode"] = mode
        result["mode_ok"] = mode == "0o600"
    return result


def _scan_cli_availability(repo_root: Path) -> dict[str, Any]:
    """Dimension: is the CLI available and executable?"""
    findings: list[dict[str, Any]] = []
    available = _cli_available()
    if not available:
        findings.append(_finding(
            "cli_not_found",
            "Critical",
            "gpt_image_cli is not on PATH. Run `gpt_image_cli setup`.",
            ["shutil.which('gpt_image_cli') returned None"],
        ))
        return _dimension(findings)

    # Check basic --help
    rc, out, err = _run_help(["--help"])
    if rc != 0:
        findings.append(_finding(
            "cli_help_fails",
            "Critical",
            "gpt_image_cli --help failed.",
            [f"rc={rc}", err[:200]],
        ))
    else:
        subcommands = []
        for name in ["generate", "generate-batch", "edit"]:
            if name in out:
                subcommands.append(name)
        findings.append(_finding(
            "cli_subcommands",
            "Info",
            f"gpt_image_cli exposes subcommands: {', '.join(subcommands)}",
            [out[:500]],
        ))

    # Check env config
    env_info = _check_env_config()
    if not env_info["exists"]:
        findings.append(_finding(
            "env_config_missing",
            "Critical",
            "gpt_image_cli env config missing. Run `gpt_image_cli setup`.",
            [f"Expected at {env_info['path']}"],
        ))
    elif not env_info.get("mode_ok", False):
        findings.append(_finding(
            "env_config_bad_permissions",
            "Warning",
            f"gpt_image_cli env file has mode {env_info['mode']}, expected 0o600.",
            [f"Path: {env_info['path']}"],
        ))
    else:
        findings.append(_finding(
            "env_config_ok",
            "Info",
            "gpt_image_cli env config exists with correct permissions.",
            [f"Path: {env_info['path']}, mode: {env_info['mode']}"],
        ))

    return _dimension(findings)


def _scan_generate_params() -> dict[str, Any]:
    """Dimension: what parameters does `generate` support?"""
    findings: list[dict[str, Any]] = []
    rc, out, err = _run_help(["generate", "--help"])
    if rc != 0:
        findings.append(_finding(
            "generate_help_fails",
            "Critical",
            "`gpt_image_cli generate --help` failed.",
            [f"rc={rc}", err[:200]],
        ))
        return _dimension(findings)

    params = _parse_help_params(out)
    param_names = [p["name"] for p in params]

    findings.append(_finding(
        "generate_all_params",
        "Info",
        f"`generate` supports {len(params)} parameters: {', '.join(param_names)}",
        [json.dumps(params, indent=2, ensure_ascii=False)],
    ))

    # Key parameters
    key_params = {
        "--prompt": "text description for image generation",
        "--n": "number of images to generate",
        "--size": "image dimensions (e.g. 1024x1024)",
        "--quality": "image quality (low/medium/high)",
        "--background": "background mode (auto/opaque)",
        "--model": "model selection (gpt-image-2)",
        "--out": "output file path",
        "--negative": "negative prompt to exclude elements",
        "--output-format": "output format (png/jpeg/webp)",
    }
    for param, desc in key_params.items():
        if param in param_names:
            findings.append(_finding(
                f"generate_has_{param.lstrip('-').replace('-', '_')}",
                "Info",
                f"`generate` supports {param}: {desc}",
                [p["description"] for p in params if p["name"] == param][0:1],
            ))

    # Prompt augmentation parameters
    augment_params = ["--use-case", "--scene", "--subject", "--style",
                      "--composition", "--lighting", "--palette", "--materials",
                      "--text", "--constraints"]
    found_augment = [p for p in augment_params if p in param_names]
    if found_augment:
        findings.append(_finding(
            "generate_prompt_augmentation",
            "Info",
            f"`generate` supports prompt augmentation via: {', '.join(found_augment)}",
            [],
        ))

    # Negative prompt
    if "--negative" in param_names:
        findings.append(_finding(
            "generate_negative_prompt",
            "Info",
            "`generate` supports negative prompt (`--negative`) for element exclusion.",
            [],
        ))

    return _dimension(findings)


def _scan_edit_params() -> dict[str, Any]:
    """Dimension: what parameters does `edit` support for image-to-image?"""
    findings: list[dict[str, Any]] = []
    rc, out, err = _run_help(["edit", "--help"])
    if rc != 0:
        findings.append(_finding(
            "edit_help_fails",
            "Critical",
            "`gpt_image_cli edit --help` failed.",
            [f"rc={rc}", err[:200]],
        ))
        return _dimension(findings)

    params = _parse_help_params(out)
    param_names = [p["name"] for p in params]

    # Check for --image (required for editing)
    if "--image" in param_names:
        findings.append(_finding(
            "edit_has_image_param",
            "Info",
            "`edit` supports `--image` for source image input (image-to-image editing).",
            [p["description"] for p in params if p["name"] == "--image"][0:1],
        ))
    else:
        findings.append(_finding(
            "edit_missing_image_param",
            "Warning",
            "`edit` does not document an `--image` parameter.",
            [],
        ))

    # Check for --mask (inpainting)
    if "--mask" in param_names:
        findings.append(_finding(
            "edit_has_mask_param",
            "Info",
            "`edit` supports `--mask` for inpainting (selective region editing).",
            [p["description"] for p in params if p["name"] == "--mask"][0:1],
        ))

    # Check for --input-fidelity
    if "--input-fidelity" in param_names:
        findings.append(_finding(
            "edit_has_input_fidelity",
            "Info",
            "`edit` supports `--input-fidelity` to control how closely to follow the input image.",
            [p["description"] for p in params if p["name"] == "--input-fidelity"][0:1],
        ))

    findings.append(_finding(
        "edit_all_params",
        "Info",
        f"`edit` supports {len(params)} parameters total (includes all generate params plus edit-specific ones).",
        [json.dumps(params, indent=2, ensure_ascii=False)],
    ))

    return _dimension(findings)


def _scan_batch_params() -> dict[str, Any]:
    """Dimension: what parameters does `generate-batch` support?"""
    findings: list[dict[str, Any]] = []
    rc, out, err = _run_help(["generate-batch", "--help"])
    if rc != 0:
        findings.append(_finding(
            "batch_help_fails",
            "Warning",
            "`gpt_image_cli generate-batch --help` failed.",
            [f"rc={rc}", err[:200]],
        ))
        return _dimension(findings)

    params = _parse_help_params(out)
    param_names = [p["name"] for p in params]

    batch_specific = {
        "--input": "JSONL input file path",
        "--concurrency": "parallel generation concurrency",
        "--max-attempts": "max retry attempts per prompt",
        "--fail-fast": "stop on first failure",
    }
    for param, desc in batch_specific.items():
        if param in param_names:
            findings.append(_finding(
                f"batch_has_{param.lstrip('-').replace('-', '_')}",
                "Info",
                f"`generate-batch` supports {param}: {desc}",
                [p["description"] for p in params if p["name"] == param][0:1],
            ))

    return _dimension(findings)


def scan_repo(repo_root: Path) -> dict[str, Any]:
    """Run the full image gen CLI audit and return a structured report."""
    dimensions: dict[str, dict[str, Any]] = {}

    dimensions["cli_availability"] = _scan_cli_availability(repo_root)
    dimensions["generate_params"] = _scan_generate_params()
    dimensions["edit_params"] = _scan_edit_params()
    dimensions["batch_params"] = _scan_batch_params()

    total_findings = sum(d["finding_count"] for d in dimensions.values())
    total_critical = sum(d["critical_count"] for d in dimensions.values())
    total_warning = sum(d["warning_count"] for d in dimensions.values())
    total_info = sum(d["info_count"] for d in dimensions.values())

    return {
        "summary": {
            "dimensions": len(dimensions),
            "findings_total": total_findings,
            "critical_total": total_critical,
            "warning_total": total_warning,
            "info_total": total_info,
        },
        "dimensions": dimensions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit gpt_image_cli capabilities")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    repo_root = Path.cwd()
    report = scan_repo(repo_root)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"gpt_image_cli Capability Audit")
        print(f"{'=' * 50}")
        print(f"Dimensions: {report['summary']['dimensions']}")
        print(f"Findings:   {report['summary']['findings_total']}")
        print(f"  Critical: {report['summary']['critical_total']}")
        print(f"  Warning:  {report['summary']['warning_total']}")
        print(f"  Info:     {report['summary']['info_total']}")
        print()

        for dim_name, dim in report["dimensions"].items():
            print(f"--- {dim_name} ({dim['finding_count']} findings) ---")
            for f in dim["findings"]:
                print(f"  [{f['severity']}] {f['id']}: {f['summary']}")
                for e in f["evidence"][:2]:
                    print(f"    > {e[:120]}")
            print()


if __name__ == "__main__":
    main()
