from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
import yaml

from hypo_agent.core.config_loader import get_agent_root, get_memory_dir

logger = structlog.get_logger("hypo_agent.core.directory_index")

DEFAULT_SCAN_TARGETS = ("src", "config", "web/src", "scripts", "deploy", "tests")
DEFAULT_MAX_AGE = timedelta(hours=1)
IGNORED_NAMES = {".git", "__pycache__", "node_modules", "run"}
IGNORED_SUFFIXES = {".pyc"}
IGNORED_RELATIVE_PATH_PREFIXES = ("web/dist", "memory/sessions")


async def refresh_directory_index(
    *,
    agent_root: Path | str | None = None,
    index_file: Path | str | None = None,
    scan_targets: tuple[str, ...] = DEFAULT_SCAN_TARGETS,
    max_age: timedelta = DEFAULT_MAX_AGE,
    force: bool = False,
) -> bool:
    return await asyncio.to_thread(
        refresh_directory_index_sync,
        agent_root=agent_root,
        index_file=index_file,
        scan_targets=scan_targets,
        max_age=max_age,
        force=force,
    )


def refresh_directory_index_sync(
    *,
    agent_root: Path | str | None = None,
    index_file: Path | str | None = None,
    scan_targets: tuple[str, ...] = DEFAULT_SCAN_TARGETS,
    max_age: timedelta = DEFAULT_MAX_AGE,
    force: bool = False,
) -> bool:
    resolved_root = (
        Path(agent_root).expanduser().resolve(strict=False)
        if agent_root is not None
        else get_agent_root()
    )
    resolved_index_file = (
        Path(index_file).expanduser().resolve(strict=False)
        if index_file is not None
        else get_memory_dir() / "knowledge" / "directory_index.yaml"
    )

    if not force and _is_fresh(resolved_index_file, max_age=max_age):
        logger.info(
            "directory_index.skip_fresh",
            index_file=str(resolved_index_file),
            max_age_seconds=int(max_age.total_seconds()),
        )
        return False

    existing_payload = load_directory_index_payload(resolved_index_file)
    payload = build_directory_index_payload(
        resolved_root,
        scan_targets=scan_targets,
        existing_payload=existing_payload,
    )

    resolved_index_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_index_file.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    logger.info(
        "directory_index.refreshed",
        agent_root=str(resolved_root),
        index_file=str(resolved_index_file),
        roots=len(payload["directories"]),
        total_files=payload["total_files"],
    )
    return True


def load_directory_index_payload(index_file: Path | str) -> dict[str, Any]:
    resolved = Path(index_file).expanduser().resolve(strict=False)
    if not resolved.exists():
        return {"directories": {}}

    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {"directories": {}}
    directories = raw.get("directories")
    if not isinstance(directories, dict):
        raw["directories"] = {}
    return raw


def build_directory_index_payload(
    agent_root: Path | str,
    *,
    scan_targets: tuple[str, ...] = DEFAULT_SCAN_TARGETS,
    existing_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = Path(agent_root).expanduser().resolve(strict=False)
    existing_directories = (
        existing_payload.get("directories", {})
        if isinstance(existing_payload, dict)
        else {}
    )
    if not isinstance(existing_directories, dict):
        existing_directories = {}

    directories: dict[str, Any] = {}
    for relative_target in scan_targets:
        target_root = (resolved_root / relative_target).resolve(strict=False)
        if not target_root.exists() or not target_root.is_dir():
            continue

        root_key = str(target_root)
        new_tree = build_directory_tree(target_root, agent_root=resolved_root)
        existing_tree = existing_directories.get(root_key)
        if isinstance(existing_tree, dict):
            new_tree = merge_directory_descriptions(new_tree, existing_tree)
        directories[root_key] = new_tree

    return {
        "agent_root": str(resolved_root),
        "generated_at": datetime.now(UTC).isoformat(),
        "last_scan": datetime.now(UTC).isoformat(),
        "scan_targets": list(scan_targets),
        "total_files": sum(_count_files(node) for node in directories.values() if isinstance(node, dict)),
        "directories": directories,
    }


def build_directory_tree(
    root: Path | str,
    *,
    agent_root: Path | str | None = None,
    max_depth: int | None = None,
    _depth: int = 1,
) -> dict[str, Any]:
    resolved_root = Path(root).expanduser().resolve(strict=False)
    resolved_agent_root = (
        Path(agent_root).expanduser().resolve(strict=False)
        if agent_root is not None
        else get_agent_root()
    )

    files: list[dict[str, Any]] = []
    children: dict[str, Any] = {}
    for entry in sorted(resolved_root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if _should_ignore(entry, agent_root=resolved_agent_root):
            continue

        if entry.is_file():
            files.append(_build_file_entry(entry, agent_root=resolved_agent_root))
            continue

        if entry.is_dir():
            if max_depth is not None and _depth >= max_depth:
                children[entry.name] = _build_directory_stub(entry, agent_root=resolved_agent_root)
                continue

            children[entry.name] = build_directory_tree(
                entry,
                agent_root=resolved_agent_root,
                max_depth=max_depth,
                _depth=_depth + 1,
            )

    stat = resolved_root.stat()
    return {
        "description": "",
        "name": resolved_root.name,
        "path": _relative_path(resolved_root, agent_root=resolved_agent_root),
        "absolute_path": str(resolved_root),
        "modified_time": _isoformat(stat.st_mtime),
        "file_count": len(files),
        "files": files,
        "children": children,
    }


def merge_directory_descriptions(
    fresh: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    existing_description = existing.get("description")
    if isinstance(existing_description, str) and existing_description and not fresh.get("description"):
        fresh["description"] = existing_description

    fresh_children = fresh.get("children")
    existing_children = existing.get("children")
    if not isinstance(fresh_children, dict) or not isinstance(existing_children, dict):
        return fresh

    for child_name, child_node in fresh_children.items():
        existing_child = existing_children.get(child_name)
        if isinstance(child_node, dict) and isinstance(existing_child, dict):
            fresh_children[child_name] = merge_directory_descriptions(child_node, existing_child)
    return fresh


def _count_files(node: dict[str, Any]) -> int:
    total = 0
    files = node.get("files")
    if isinstance(files, list):
        total += len(files)
    children = node.get("children")
    if isinstance(children, dict):
        for child in children.values():
            if isinstance(child, dict):
                total += _count_files(child)
    return total


def _build_file_entry(path: Path, *, agent_root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": _relative_path(path, agent_root=agent_root),
        "absolute_path": str(path),
        "size": stat.st_size,
        "modified_time": _isoformat(stat.st_mtime),
    }


def _build_directory_stub(path: Path, *, agent_root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "description": "",
        "name": path.name,
        "path": _relative_path(path, agent_root=agent_root),
        "absolute_path": str(path),
        "modified_time": _isoformat(stat.st_mtime),
        "file_count": 0,
        "files": [],
        "children": {},
    }


def _relative_path(path: Path, *, agent_root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(agent_root).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _should_ignore(path: Path, *, agent_root: Path) -> bool:
    if path.name in IGNORED_NAMES:
        return True
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True

    relative_path = _relative_path(path, agent_root=agent_root)
    return any(
        relative_path == ignored or relative_path.startswith(f"{ignored}/")
        for ignored in IGNORED_RELATIVE_PATH_PREFIXES
    )


def _is_fresh(index_file: Path, *, max_age: timedelta) -> bool:
    if not index_file.exists():
        return False
    modified_at = datetime.fromtimestamp(index_file.stat().st_mtime, tz=UTC)
    return datetime.now(UTC) - modified_at <= max_age


def _isoformat(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
