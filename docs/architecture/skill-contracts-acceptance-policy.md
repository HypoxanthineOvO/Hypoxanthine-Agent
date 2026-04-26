# Skill Contracts And Acceptance Policy

Date: 2026-04-26

## Purpose

Skill tests should distinguish "the Python method works in a narrow unit test" from "the skill is safe and usable in a deployed conversation." Hypo-Agent now exposes runtime skill contracts and acceptance gates so each model-facing tool has operational metadata and an explicit verification surface.

## Contract Fields

Each `ToolContract` records:

- `tool_name`
- `description`
- `parameters_schema`
- `operation`
- `side_effect_class`
- `timeout_seconds`
- `retryable`
- `required_config`
- `repair_hints_zh`
- `acceptance_probes`

The contract is generated from registered `BaseSkill.tools` and inferred metadata in `hypo_agent.core.skill_contracts`. This is intentionally compatibility-first: existing skill schemas remain valid, while missing contract metadata is surfaced through contract validation and acceptance reports.

## Gate Types

| Gate | Meaning | Default Behavior |
| --- | --- | --- |
| `unit` | Narrow Python tests for the skill implementation | Required for every tool |
| `contract` | Contract/model-facing metadata validation | Required for every tool |
| `test_mode_probe` | Deterministic probe that must run under `HYPO_TEST_MODE=1` | Required for every tool |
| `optional_integration` | Real or sandbox external service gate | Optional; never runs by default |

Test-mode probes must not target production port `8765`, must not use connected QQ, and must be safe to run against `test/sandbox/`.

## Runtime Access

`SkillManager` exposes:

- `get_skill_contracts()`
- `get_skill_acceptance_report()`

`scripts/verify_skills.py` includes an `Acceptance Gates` section in its markdown output. The report shows defined unit, contract, test-mode probe, and optional integration gates across the runtime skill registry.

## Notion Schema-Sensitive Tools

Notion property writes now validate model-provided property names against the local database/page schema before calling the remote API. Unknown fields return a Chinese repair hint instructing the model to call `notion_get_schema` and retry with an exact field name.

This prevents common schema mismatch errors from being sent to Notion as invalid remote requests, and lets R1 classify them as recoverable user/model input errors instead of external tool bugs.

## Limitations

- Contract metadata is inferred in R2; future work can move overrides into explicit manifests.
- The acceptance report verifies gate definitions, not the result of every gate command.
- Optional integration gates are listed but intentionally not executed by default.
