# Skills Phase 3 Acceptance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add internal SKILL.md usage contracts and perform end-to-end verification for all Phase 1-3 skills.

**Architecture:** Keep Python execution backends unchanged. Add documentation-only `SKILL.md` files for internal skills, then verify the whole `skills/` catalog with a repo-level script plus focused tests for matching, tool alignment, and trigger conflicts.

**Tech Stack:** Python, pytest, YAML frontmatter, SkillCatalog, SkillManager

---

### Task 1: Add plan artifact

**Files:**
- Create: `docs/plans/2026-04-03-skills-phase3-acceptance-implementation-plan.md`

**Step 1: Save the approved Phase 3 plan**

Record the implementation approach, scope, and verification sequence in this file.

**Step 2: Use the saved plan as execution reference**

No extra code changes in this task.

### Task 2: Add internal usage contracts

**Files:**
- Create: `skills/internal/exec/SKILL.md`
- Create: `skills/internal/code-run/SKILL.md`
- Create: `skills/internal/filesystem/SKILL.md`
- Create: `skills/internal/memory/SKILL.md`
- Create: `skills/internal/tmux/SKILL.md`
- Create: `skills/internal/reminder/SKILL.md`
- Create: `skills/internal/email-scanner/SKILL.md`

**Step 1: Write SKILL frontmatter**

Use the repo `skills/SPEC.md` format and keep `hypo.category` as `internal`.

**Step 2: Write usage-contract body**

Cover tool roles, usage sequence, safety boundaries, and what not to do.

**Step 3: Keep Python code untouched**

Do not modify internal skill backends during this task.

### Task 3: Build repo-level verification

**Files:**
- Create: `scripts/verify_skills.py`

**Step 1: Scan all SKILL.md files**

Validate frontmatter, body, references, allowed tools, and exec profile existence.

**Step 2: Build a runtime tool registry**

Instantiate a minimal `SkillManager` with test doubles so all expected tool names are registered without depending on external services.

**Step 3: Add trigger conflict analysis**

Collect shared triggers across pure/hybrid skills and print a structured conflict section.

### Task 4: Add repository tests

**Files:**
- Modify: `tests/core/test_skill_catalog_repo.py`
- Create: `tests/core/test_skill_verification.py`

**Step 1: Add all 12 pure/hybrid candidate-match tests**

Use the user-provided trigger messages and verify match + body + key tool name.

**Step 2: Add tool-alignment and profile tests**

Check every SKILL manifest against a runtime tool registry and `config/exec_profiles.yaml`.

**Step 3: Add verification-script smoke coverage**

Run the verification logic from tests and assert it reports success for the current repo.

### Task 5: Update index and run acceptance

**Files:**
- Modify: `skills/index.md`

**Step 1: Add internal skills to the human-readable index**

Keep descriptions concise.

**Step 2: Run acceptance commands**

Run:

```bash
python scripts/verify_skills.py
pytest -q
```

**Step 3: Produce final acceptance report**

Summarize catalog completeness, injection coverage, tool alignment, conflict analysis, and any residual issues.
