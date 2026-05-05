from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_system_contracts.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_system_contracts", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_system_contracts_reports_required_architecture_dimensions() -> None:
    module = _load_module()

    report = module.scan_repo(REPO_ROOT)

    assert report["summary"]["dimensions"] >= 5
    assert report["summary"]["findings_total"] >= 5
    assert "resource_reference" in report["dimensions"]
    assert "tool_recovery" in report["dimensions"]
    assert "webpage_reading" in report["dimensions"]
    assert "channel_file_delivery" in report["dimensions"]
    assert "frontend_observability" in report["dimensions"]


def test_audit_system_contracts_finds_current_first_principles_gaps() -> None:
    module = _load_module()

    report = module.scan_repo(REPO_ROOT)
    finding_ids = {
        finding["id"]
        for dimension in report["dimensions"].values()
        for finding in dimension["findings"]
    }

    assert "resource_ref_missing_first_class_model" in finding_ids
    assert "filesystem_missing_candidate_search" in finding_ids
    assert "tool_user_input_error_marked_non_retryable" in finding_ids
    assert "web_read_browser_fallback_missing" in finding_ids
    assert "frontend_missing_resource_candidate_confirmation" in finding_ids
