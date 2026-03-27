from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from hypo_agent.channels.coder import CoderClient, CoderUnavailableError
from hypo_agent.core.config_loader import load_secrets_config


async def main() -> int:
    try:
        secrets = load_secrets_config("config/secrets.yaml")
    except FileNotFoundError:
        print("SKIP: config/secrets.yaml 不存在")
        return 0

    services = secrets.services
    coder_cfg = services.hypo_coder if services is not None else None
    if coder_cfg is None:
        print("SKIP: services.hypo_coder 未配置")
        return 0

    base_url = str(coder_cfg.base_url or "").strip()
    agent_token = str(coder_cfg.agent_token or "").strip()
    if not base_url or not agent_token:
        print("SKIP: services.hypo_coder.base_url 或 agent_token 为空")
        return 0

    client = CoderClient(base_url=base_url, agent_token=agent_token)
    try:
        health = await client.health()
    except CoderUnavailableError as exc:
        print(f"SKIP: {exc}")
        return 0

    print(f"✅ Health: {health}")

    test_dir = Path(tempfile.mkdtemp(prefix="coder_e2e_"))
    hello_path = test_dir / "hello.py"
    hello_path.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    prompt = (
        "请修改 hello.py，让 greet() 返回 'hello world' 而不是 'hello'。"
        "修改完成后用 python -c \"from hello import greet; assert greet() == 'hello world'\" 验证。"
    )

    task = await client.create_task(
        prompt=prompt,
        working_directory=str(test_dir),
        model="o4-mini",
        approval_policy="full-auto",
        webhook=str(coder_cfg.webhook_url or "").strip() or None,
    )
    task_id = str(task.get("taskId") or "").strip()
    print(f"✅ Task created: {task_id} status={task.get('status')}")

    status: dict = task
    for _ in range(24):
        await asyncio.sleep(5)
        status = await client.get_task(task_id)
        print(f"  • status={status.get('status')}")
        if str(status.get("status") or "").strip() in {"completed", "failed", "aborted"}:
            break

    if str(status.get("status") or "").strip() == "completed":
        result = status.get("result") if isinstance(status.get("result"), dict) else {}
        print("✅ Task completed!")
        print(f"  Summary: {result.get('summary', 'N/A')}")
        print(f"  Files: {result.get('fileChanges', [])}")
        print(f"  Tests: {result.get('testsPassed', 'N/A')}")
    else:
        print(f"❌ Task ended with status={status.get('status')}")
        if status.get("error"):
            print(f"  Error: {status.get('error')}")
        return 1

    content = hello_path.read_text(encoding="utf-8")
    print(f"\n最终文件内容:\n{content}")
    if "hello world" not in content:
        raise AssertionError("文件未被正确修改！")
    print("✅ E2E 验证通过！")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
