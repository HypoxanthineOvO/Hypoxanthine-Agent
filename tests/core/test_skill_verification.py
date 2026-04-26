from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_verify_module():
    module_path = REPO_ROOT / "scripts" / "verify_skills.py"
    spec = importlib.util.spec_from_file_location("verify_skills_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verify_skills_repo_report_passes_for_current_repo() -> None:
    module = _load_verify_module()

    report = module.verify_repo(REPO_ROOT)

    assert report.ok is True
    assert report.skill_checks
    assert report.pipeline_checks


def test_verify_skills_runtime_registry_contains_all_allowed_tools() -> None:
    module = _load_verify_module()

    report = module.verify_repo(REPO_ROOT)
    failures = [item for item in report.skill_checks if not item.tools_ok]

    assert failures == []


def test_verify_skills_has_no_severe_trigger_conflicts() -> None:
    module = _load_verify_module()

    report = module.verify_repo(REPO_ROOT)
    severe = [item for item in report.conflicts if item.severity == "severe"]

    assert severe == []


def test_verify_skills_includes_contract_acceptance_report() -> None:
    module = _load_verify_module()

    report = module.verify_repo(REPO_ROOT)

    assert report.acceptance_report["summary"]["unit"]["total"] > 0
    assert report.acceptance_report["summary"]["contract"]["total"] > 0
    assert report.acceptance_report["summary"]["test_mode_probe"]["total"] > 0
    assert report.acceptance_report["skills"]
