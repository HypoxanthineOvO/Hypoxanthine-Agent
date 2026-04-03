from __future__ import annotations

from pathlib import Path

from hypo_agent.core.skill_catalog import SkillCatalog


def _write_skill(root: Path, name: str, trigger: str) -> None:
    skill_dir = root / "pure" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: "{name}"
description: "Test skill"
compatibility: "linux"
allowed-tools: "exec_command, read_file"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "git"
  hypo.triggers: "{trigger}"
  hypo.risk: "low"
  hypo.dependencies: "git"
---

Use this skill.
""",
        encoding="utf-8",
    )
    references_dir = skill_dir / "references"
    references_dir.mkdir(exist_ok=True)
    (references_dir / "guide.txt").write_text("hello", encoding="utf-8")


def test_skill_catalog_scans_and_parses_frontmatter(tmp_path: Path) -> None:
    _write_skill(tmp_path, "git-workflow", "git,commit")
    catalog = SkillCatalog(tmp_path)

    catalog.scan()

    manifest = catalog.list_manifests()[0]
    assert manifest.name == "git-workflow"
    assert manifest.allowed_tools == ["exec_command", "read_file"]
    assert manifest.exec_profile == "git"


def test_skill_catalog_matches_candidates_by_trigger(tmp_path: Path) -> None:
    _write_skill(tmp_path, "git-workflow", "git,commit")
    _write_skill(tmp_path, "host-inspection", "disk,memory")
    catalog = SkillCatalog(tmp_path)
    catalog.scan()

    candidates = catalog.match_candidates("please check git status before commit")

    assert [item.name for item in candidates] == ["git-workflow"]


def test_skill_catalog_lazy_loads_body_and_references(tmp_path: Path) -> None:
    _write_skill(tmp_path, "git-workflow", "git")
    catalog = SkillCatalog(tmp_path)
    catalog.scan()

    body = catalog.load_body("git-workflow")
    refs = catalog.load_references("git-workflow")

    assert "Use this skill." in body
    assert refs == {"guide.txt": "hello"}
