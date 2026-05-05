"""Tests for the gpt_image_cli capability audit probe."""
from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_image_gen_cli.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_image_gen_cli", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_image_gen_cli_reports_required_dimensions() -> None:
    module = _load_module()
    report = module.scan_repo(REPO_ROOT)

    assert report["summary"]["dimensions"] >= 4
    assert report["summary"]["findings_total"] >= 4
    assert "cli_availability" in report["dimensions"]
    assert "generate_params" in report["dimensions"]
    assert "edit_params" in report["dimensions"]
    assert "batch_params" in report["dimensions"]


def test_audit_image_gen_cli_discovers_cli_status() -> None:
    module = _load_module()
    report = module.scan_repo(REPO_ROOT)

    cli_dim = report["dimensions"]["cli_availability"]
    finding_ids = {f["id"] for f in cli_dim["findings"]}
    # If CLI is available, we expect subcommands info
    # If CLI is missing, we expect cli_not_found
    assert "cli_not_found" in finding_ids or "cli_subcommands" in finding_ids


def test_audit_image_gen_cli_generate_params_has_key_params() -> None:
    module = _load_module()
    report = module.scan_repo(REPO_ROOT)

    gen_dim = report["dimensions"]["generate_params"]
    finding_ids = {f["id"] for f in gen_dim["findings"]}

    # If generate help worked, we should see all_params info
    if "generate_all_params" in finding_ids:
        # Key parameters should be present
        assert "generate_has_prompt" in finding_ids
        assert "generate_has_n" in finding_ids
        assert "generate_has_size" in finding_ids
        assert "generate_has_quality" in finding_ids


def test_audit_image_gen_cli_edit_has_image_param() -> None:
    module = _load_module()
    report = module.scan_repo(REPO_ROOT)

    edit_dim = report["dimensions"]["edit_params"]
    finding_ids = {f["id"] for f in edit_dim["findings"]}

    if "edit_all_params" in finding_ids:
        # edit subcommand should have --image for image-to-image
        assert "edit_has_image_param" in finding_ids


def test_audit_image_gen_cli_batch_params() -> None:
    module = _load_module()
    report = module.scan_repo(REPO_ROOT)

    batch_dim = report["dimensions"]["batch_params"]
    finding_ids = {f["id"] for f in batch_dim["findings"]}

    if "batch_has_input" in finding_ids:
        # batch should support concurrency and max-attempts
        assert "batch_has_concurrency" in finding_ids


def test_parse_help_params_extracts_parameters() -> None:
    module = _load_module()

    sample_help = """options:
  --model MODEL
  --prompt PROMPT
  --n N
  --size SIZE
  --quality QUALITY
"""
    params = module._parse_help_params(sample_help)
    assert len(params) == 5
    assert params[0]["name"] == "--model"
    assert params[1]["name"] == "--prompt"
    assert params[2]["name"] == "--n"


def test_check_env_config_reports_path() -> None:
    module = _load_module()
    env_info = module._check_env_config()

    assert "path" in env_info
    assert "exists" in env_info
    # env file may or may not exist in CI
    if env_info["exists"]:
        assert "mode" in env_info
        assert "mode_ok" in env_info
