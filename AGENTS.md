# AGENTS

## Project Docs

- Architecture reference: `docs/architecture.md`（进行 M1 及后续开发前先阅读，包含目录结构、模块职责与数据模型契约）
- 文档提交约定：每次里程碑（Mx）完成后，新增或更新文档需单独提交，提交信息格式必须为 `Mx[doc]: <说明>`（例如：`M3[doc]: add session memory implementation plan`）。

## Testing Default

- 默认验收链路使用测试模式，不要默认连到部署中的生产实例。
- 默认启动命令：`bash test_run.sh`（内部会设置 `HYPO_TEST_MODE=1`，使用 `test/sandbox/`，端口 `8766`）。
- 默认 smoke 命令：`HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`
- 测试模式下 QQ adapter 不注册，不应给真实 QQ 发消息；如需验证部署实例，必须显式说明这是“生产/部署验收”，不要与默认 smoke 混用。

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>frontend-design</name>
<description>Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, artifacts, posters, or applications (examples include websites, landing pages, dashboards, React components, HTML/CSS layouts, or when styling/beautifying any web UI). Generates creative, polished code and UI design that avoids generic AI aesthetics.</description>
<location>project</location>
</skill>

<skill>
<name>web-artifacts-builder</name>
<description>Suite of tools for creating elaborate, multi-component claude.ai HTML artifacts using modern frontend web technologies (React, Tailwind CSS, shadcn/ui). Use for complex artifacts requiring state management, routing, or shadcn/ui components - not for simple single-file HTML/JSX artifacts.</description>
<location>project</location>
</skill>

<skill>
<name>webapp-testing</name>
<description>Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.</description>
<location>project</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
