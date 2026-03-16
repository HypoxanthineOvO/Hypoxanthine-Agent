from __future__ import annotations

from pathlib import Path
import tomllib


def test_pyproject_uses_hatchling_and_python_312_baseline() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert payload["build-system"]["build-backend"] == "hatchling.build"
    assert "hatchling" in payload["build-system"]["requires"][0]
    assert payload["project"]["name"] == "hypo-agent"
    assert payload["project"]["requires-python"] == ">=3.12"


def test_pyproject_declares_core_runtime_and_dev_dependencies() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(payload["project"]["dependencies"])
    dev_dependencies = set(payload["dependency-groups"]["dev"])

    assert "fastapi>=0.115.0,<1.0.0" in dependencies
    assert "uvicorn[standard]>=0.30.0,<1.0.0" in dependencies
    assert "aiosqlite>=0.20.0,<1.0.0" in dependencies
    assert "sqlite-vec>=0.1.6,<0.2.0" in dependencies
    assert "litellm>=1.60.0,<2.0.0" in dependencies
    assert "websockets>=16.0,<17.0" in dependencies
    assert "tiktoken>=0.12.0,<1.0.0" in dependencies
    assert "pytest>=8.0.0,<9.0.0" in dev_dependencies
    assert "pytest-cov>=5.0.0,<6.0.0" in dev_dependencies


def test_uv_lock_and_test_run_script_exist_and_use_uv() -> None:
    assert Path("uv.lock").exists() is True

    test_run = Path("test_run.sh").read_text(encoding="utf-8")
    assert "uv run python -m hypo_agent" in test_run
