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
  hypo.exec_profile: "git|systemd|python-dev|hypo-agent|host-inspect"
  hypo.triggers: "comma,separated,keywords"
  hypo.risk: "low|medium|high"
  hypo.dependencies: "git,uv,journalctl"
---
```

Rules:

- `name`, `description`, and `allowed-tools` are required.
- `name` should be kebab-case.
- `allowed-tools` may be comma-separated or whitespace-separated.
- `metadata.hypo.triggers` should contain short phrases the Pipeline can keyword-match.
- `metadata.hypo.exec_profile` is required for pure CLI skills that use `exec_command` or `exec_script`.
- Keep Markdown bodies task-oriented: concrete command sequence, safety rules, and output interpretation.
