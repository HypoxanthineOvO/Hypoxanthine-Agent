from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from hypo_agent.core.directory_index import refresh_directory_index, refresh_directory_index_sync


def test_refresh_directory_index_writes_project_tree_with_file_metadata(tmp_path: Path) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    src_dir = repo_root / "src" / "hypo_agent"
    config_dir = repo_root / "config"
    web_src_dir = repo_root / "web" / "src"
    scripts_dir = repo_root / "scripts"
    deploy_dir = repo_root / "deploy"
    tests_dir = repo_root / "tests"

    for directory in (src_dir, config_dir, web_src_dir, scripts_dir, deploy_dir, tests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app_py = src_dir / "app.py"
    app_py.write_text("print('hello')\n", encoding="utf-8")
    (config_dir / "persona.yaml").write_text("name: Hypo\n", encoding="utf-8")
    (web_src_dir / "main.ts").write_text("console.log('hi')\n", encoding="utf-8")

    index_file = repo_root / "memory" / "knowledge" / "directory_index.yaml"

    wrote = refresh_directory_index_sync(agent_root=repo_root, index_file=index_file)

    assert wrote is True
    payload = yaml.safe_load(index_file.read_text(encoding="utf-8"))
    src_key = str((repo_root / "src").resolve(strict=False))
    src_node = payload["directories"][src_key]
    package_node = src_node["children"]["hypo_agent"]
    app_entry = package_node["files"][0]

    assert payload["agent_root"] == str(repo_root.resolve(strict=False))
    assert "generated_at" in payload
    assert src_node["path"] == "src"
    assert package_node["path"] == "src/hypo_agent"
    assert app_entry["name"] == "app.py"
    assert app_entry["path"] == "src/hypo_agent/app.py"
    assert app_entry["size"] == app_py.stat().st_size
    assert "modified_time" in app_entry


def test_refresh_directory_index_ignores_runtime_and_build_artifacts(tmp_path: Path) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    src_dir = repo_root / "src"
    web_src_dir = repo_root / "web" / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    web_src_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "__pycache__").mkdir(parents=True, exist_ok=True)
    (src_dir / "__pycache__" / "ignored.pyc").write_bytes(b"pyc")
    (web_src_dir / "node_modules").mkdir(parents=True, exist_ok=True)
    (web_src_dir / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")
    (src_dir / "keep.py").write_text("x = 1\n", encoding="utf-8")

    index_file = repo_root / "memory" / "knowledge" / "directory_index.yaml"
    refresh_directory_index_sync(agent_root=repo_root, index_file=index_file)

    payload = yaml.safe_load(index_file.read_text(encoding="utf-8"))
    src_key = str(src_dir.resolve(strict=False))
    src_node = payload["directories"][src_key]
    src_children = src_node["children"]
    web_node = payload["directories"][str(web_src_dir.resolve(strict=False))]

    assert "__pycache__" not in src_children
    assert "node_modules" not in web_node["children"]
    assert [item["name"] for item in src_node["files"]] == ["keep.py"]


def test_refresh_directory_index_skips_fresh_existing_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    (repo_root / "src").mkdir(parents=True, exist_ok=True)
    index_file = repo_root / "memory" / "knowledge" / "directory_index.yaml"
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text("agent_root: existing\n", encoding="utf-8")

    wrote = refresh_directory_index_sync(
        agent_root=repo_root,
        index_file=index_file,
        max_age=timedelta(hours=1),
    )

    assert wrote is False
    assert index_file.read_text(encoding="utf-8") == "agent_root: existing\n"


def test_refresh_directory_index_rewrites_stale_existing_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    (repo_root / "src").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
    index_file = repo_root / "memory" / "knowledge" / "directory_index.yaml"
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text("agent_root: existing\n", encoding="utf-8")

    stale_time = datetime.now(UTC) - timedelta(hours=2)
    timestamp = stale_time.timestamp()
    index_file.touch()
    Path(index_file).chmod(0o644)
    import os
    os.utime(index_file, (timestamp, timestamp))

    wrote = asyncio.run(
        refresh_directory_index(
            agent_root=repo_root,
            index_file=index_file,
            max_age=timedelta(hours=1),
        )
    )

    assert wrote is True
    payload = yaml.safe_load(index_file.read_text(encoding="utf-8"))
    assert payload["agent_root"] == str(repo_root.resolve(strict=False))
