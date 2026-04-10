# Skill Catalog Spec

`SKILL.md` files use YAML frontmatter followed by Markdown instructions.

```yaml
---
name: "skill-name"
description: "One-line description"
compatibility: "linux"
allowed-tools: "exec_command read_file"
metadata:
  hypo.category: "pure|hybrid|internal"
  hypo.backend: "none|exec|notion|coder|info"
  hypo.exec_profile: "git|systemd|python-dev|hypo-agent|host-inspect|log-inspect|cli-json"
  hypo.triggers: "comma,separated,keywords"
  hypo.risk: "low|medium|high"
  hypo.dependencies: "git,uv,journalctl"
  hypo.cli_package: "@scope/package|package-name"
  hypo.cli_commands: "tool-name another-tool"
  hypo.io_format: "json-stdio|text"
---
```

Rules:

- `name`, `description`, and `allowed-tools` are required.
- `name` should be kebab-case.
- `allowed-tools` may be comma-separated or whitespace-separated.
- `metadata.hypo.triggers` should contain short phrases the Pipeline can keyword-match.
- `metadata.hypo.exec_profile` is required for pure CLI skills that use `exec_command` or `exec_script`.
- For external package CLI skills, prefer `metadata.hypo.exec_profile: "cli-json"` and declare the actual executable names in `metadata.hypo.cli_commands`.
- `metadata.hypo.cli_package` records the npm/pip package name used to install the CLI.
- `metadata.hypo.cli_commands` records the PATH-visible command names exposed by that package.
- `metadata.hypo.io_format` declares the expected interaction mode: `json-stdio` for structured JSON over stdio, `text` for plain text.
- When `SkillCatalog` availability checking is enabled, skills with declared `cli_commands` are marked unavailable and skipped from candidate matching if any declared command is missing from `PATH`.
- Keep Markdown bodies task-oriented: concrete command sequence, safety rules, and output interpretation.
