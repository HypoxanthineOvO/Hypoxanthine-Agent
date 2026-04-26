# R2 Skill Contracts And Acceptance Probes

Date: 2026-04-26

## Summary

R2 added a compatibility-first Skill contract layer, attached acceptance reporting to the runtime SkillManager and `scripts/verify_skills.py`, and added a local Notion schema validator that blocks unknown property names before remote API calls.

This makes "skill acceptance" more explicit: unit checks, contract checks, deterministic test-mode probes, and optional integration gates are now separate categories instead of one undifferentiated "tests passed" claim.

## Changes

- Added `src/hypo_agent/core/skill_contracts.py` with:
  - `SkillContract`
  - `ToolContract`
  - `AcceptanceProbe`
  - `ContractValidationError`
  - `build_contract_from_skill(...)`
  - `build_acceptance_report(...)`
- Added `SkillManager.get_skill_contracts()` and `SkillManager.get_skill_acceptance_report()`.
- Extended `scripts/verify_skills.py` to include `acceptance_report` and print an `Acceptance Gates` table.
- Added Notion local schema validation so `notion_create_entry` / property conversion rejects unknown schema fields before calling `create_page` or `update_page_properties`.
- Added architecture documentation in `docs/architecture/skill-contracts-acceptance-policy.md`.

## Tests

RED phase:

- `tests/skills/test_skill_contracts.py` initially failed on missing `hypo_agent.core.skill_contracts`.
- `tests/skills/test_notion_skill.py::test_create_entry_rejects_unknown_property_before_remote_api_call` captured the missing local validator behavior.
- `tests/skills/test_skill_manager.py::test_skill_manager_exports_contracts_and_acceptance_report` captured missing SkillManager integration.
- `tests/core/test_skill_verification.py::test_verify_skills_includes_contract_acceptance_report` captured missing `verify_skills.py` reporting.

GREEN / regression commands:

```bash
uv run pytest tests/skills/test_skill_contracts.py tests/skills/test_notion_skill.py tests/skills/test_skill_manager.py -q
uv run pytest tests/core/test_skill_verification.py tests/skills/test_skill_contracts.py tests/skills/test_notion_skill.py tests/skills/test_skill_manager.py -q
uv run pytest tests/skills tests/gateway/test_app_test_mode.py tests/scripts/test_agent_cli_smoke_qq.py -q
uv run python scripts/verify_skills.py
python -m py_compile src/hypo_agent/core/skill_contracts.py src/hypo_agent/core/skill_manager.py scripts/verify_skills.py src/hypo_agent/skills/notion_skill.py
git diff --check
```

Observed results:

- Focused R2 suite: 56 passed.
- Prompt-suggested skills/test-mode/smoke-guard suite: 241 passed.
- `scripts/verify_skills.py`: exit 0, `ok: true`.
- Acceptance gate summary from `verify_skills.py`:
  - `unit`: 50 total, 50 defined
  - `contract`: 50 total, 50 defined
  - `test_mode_probe`: 50 total, 50 defined
  - `optional_integration`: 12 total, 12 optional, 38 missing by design

Warnings are existing `lark_oapi`, `websockets`, and deprecated NapCat QQ channel warnings.

## Evaluation

| Metric | Score | Notes |
| --- | ---: | --- |
| diff_score | 2 | Adds a new contract module and reporting path; still scoped to skill acceptance |
| code_quality | 4 | Contract/reporting logic is isolated and backward compatible |
| test_coverage | 2 | Covers contracts, manager integration, Notion validator, verify script, and broad skills suite |
| complexity | 3 | Inference is heuristic but deliberately conservative for compatibility |
| architecture_drift | 1 | Matches roadmap: manifests/contracts first, explicit manifests can follow |

## Residual Risk

- Gate commands are generated and reported, not executed individually as a matrix.
- Contract metadata is inferred from tool names and skill names; explicit per-skill manifests should replace inference for high-risk tools.
- Optional integration gates remain opt-in and do not prove live external services by default.
