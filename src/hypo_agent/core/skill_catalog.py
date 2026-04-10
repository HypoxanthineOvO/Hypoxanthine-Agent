from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import shutil
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    description: str
    category: str
    path: Path
    allowed_tools: list[str]
    backend: str
    exec_profile: str | None
    triggers: list[str]
    risk: str
    dependencies: list[str]
    compatibility: str
    cli_package: str | None = None
    cli_commands: list[str] = field(default_factory=list)
    io_format: str | None = None
    available: bool = True
    unavailable_reason: str | None = None


class SkillCatalog:
    def __init__(
        self,
        skills_dir: Path,
        *,
        check_cli_availability: bool = False,
        command_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.check_cli_availability = bool(check_cli_availability)
        self.command_resolver = command_resolver or shutil.which
        self._manifests: dict[str, SkillManifest] = {}
        self._skill_files: dict[str, Path] = {}
        self._body_cache: dict[str, str] = {}
        self._references_cache: dict[str, dict[str, str]] = {}

    def scan(self) -> None:
        self._manifests.clear()
        self._skill_files.clear()
        self._body_cache.clear()
        self._references_cache.clear()
        if not self.skills_dir.exists():
            return

        for skill_file in sorted(self.skills_dir.glob("**/SKILL.md")):
            manifest = self._apply_cli_availability(self._load_manifest(skill_file))
            self._manifests[manifest.name] = manifest
            self._skill_files[manifest.name] = skill_file

    def list_manifests(self) -> list[SkillManifest]:
        return [self._manifests[name] for name in sorted(self._manifests)]

    def match_candidates(self, user_message: str) -> list[SkillManifest]:
        normalized = str(user_message or "").strip().lower()
        if not normalized:
            return []

        matched: list[tuple[int, SkillManifest]] = []
        for manifest in self._manifests.values():
            if not manifest.available:
                continue
            hit_count = sum(1 for trigger in manifest.triggers if trigger and trigger.lower() in normalized)
            if hit_count > 0:
                matched.append((hit_count, manifest))

        matched.sort(key=lambda item: (-item[0], item[1].name))
        return [manifest for _, manifest in matched]

    def load_body(self, skill_name: str) -> str:
        normalized = str(skill_name).strip()
        if normalized in self._body_cache:
            return self._body_cache[normalized]

        skill_file = self._skill_files[normalized]
        _, body = _parse_skill_file(skill_file.read_text(encoding="utf-8"))
        self._body_cache[normalized] = body
        return body

    def load_references(self, skill_name: str) -> dict[str, str]:
        normalized = str(skill_name).strip()
        if normalized in self._references_cache:
            return dict(self._references_cache[normalized])

        manifest = self._manifests[normalized]
        references_dir = manifest.path / "references"
        references: dict[str, str] = {}
        if references_dir.exists():
            for ref_path in sorted(references_dir.rglob("*")):
                if ref_path.is_file():
                    references[str(ref_path.relative_to(references_dir))] = ref_path.read_text(
                        encoding="utf-8"
                    )
        self._references_cache[normalized] = references
        return dict(references)

    def _load_manifest(self, skill_file: Path) -> SkillManifest:
        frontmatter, _ = _parse_skill_file(skill_file.read_text(encoding="utf-8"))
        metadata = frontmatter.get("metadata", {}) if isinstance(frontmatter.get("metadata"), dict) else {}

        name = str(frontmatter.get("name") or "").strip()
        if not name:
            raise ValueError(f"{skill_file} is missing frontmatter.name")

        description = str(frontmatter.get("description") or "").strip()
        if not description:
            raise ValueError(f"{skill_file} is missing frontmatter.description")

        return SkillManifest(
            name=name,
            description=description,
            category=str(metadata.get("hypo.category") or "pure").strip() or "pure",
            path=skill_file.parent,
            allowed_tools=_split_field(frontmatter.get("allowed-tools"), separator="comma_or_space"),
            backend=str(metadata.get("hypo.backend") or "none").strip() or "none",
            exec_profile=_optional_text(metadata.get("hypo.exec_profile")),
            triggers=_split_field(metadata.get("hypo.triggers"), separator=","),
            risk=str(metadata.get("hypo.risk") or "low").strip() or "low",
            dependencies=_split_field(metadata.get("hypo.dependencies"), separator=","),
            compatibility=str(frontmatter.get("compatibility") or "").strip(),
            cli_package=_optional_text(metadata.get("hypo.cli_package")),
            cli_commands=_split_field(metadata.get("hypo.cli_commands"), separator="comma_or_space"),
            io_format=_optional_text(metadata.get("hypo.io_format")),
        )

    def _apply_cli_availability(self, manifest: SkillManifest) -> SkillManifest:
        if not self.check_cli_availability or not manifest.cli_commands:
            return manifest

        missing_commands = [command for command in manifest.cli_commands if not self.command_resolver(command)]
        if not missing_commands:
            return manifest

        reason = f"missing cli command(s): {', '.join(missing_commands)}"
        return replace(
            manifest,
            available=False,
            unavailable_reason=reason,
        )


def _parse_skill_file(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must begin with YAML frontmatter")

    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("SKILL.md frontmatter is not terminated")

    frontmatter_text = parts[0][4:]
    body = parts[1].lstrip("\n")
    payload = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(payload, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    return payload, body


def _split_field(value: Any, *, separator: str = " ") -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(item).strip().strip(",") for item in value]
    else:
        raw = str(value)
        if separator == "comma_or_space":
            items = [part.strip().strip(",") for part in re.split(r"[\s,]+", raw)]
        else:
            items = [part.strip().strip(",") for part in raw.split(separator)]
    return [item for item in items if item]


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
