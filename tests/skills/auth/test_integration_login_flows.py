from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.auth.playwright_runtime import PlaywrightRuntime
from hypo_agent.skills.auth_skill import AuthSkill


def _write_minimal_secrets(path: Path) -> None:
    path.write_text(
        yaml.safe_dump({"providers": {}, "services": {"weibo": {}, "zhihu": {}, "weread": {}}}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _build_skill(tmp_path: Path) -> AuthSkill:
    secrets_path = tmp_path / "secrets.yaml"
    _write_minimal_secrets(secrets_path)
    return AuthSkill(
        structured_store=StructuredStore(tmp_path / "hypo.db"),
        secrets_path=secrets_path,
        qr_dir=tmp_path / "auth-qr",
        playwright_runtime=PlaywrightRuntime(),
        auth_check_poll_attempts=1,
        auth_check_poll_interval_seconds=0,
    )


@pytest.mark.integration
def test_weibo_integration_auth_login_requests_real_qr(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)
        try:
            output = await skill.execute("auth_login", {"platform": "weibo", "__session_id": "integration"})

            assert output.status == "success"
            assert "暂不支持" not in str(output.result)
            assert len(output.attachments) == 1
            assert Path(output.attachments[0].url).exists() is True
        finally:
            await skill.playwright_runtime.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_zhihu_integration_auth_login_requests_real_qr_or_tolerates_risk(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)
        try:
            output = await skill.execute("auth_login", {"platform": "zhihu", "__session_id": "integration"})

            if output.status == "success":
                assert len(output.attachments) == 1
                assert Path(output.attachments[0].url).exists() is True
                return

            text = f"{output.error_info} {output.result}"
            assert "40352" in text or "风控" in text or "安全验证" in text
        finally:
            await skill.playwright_runtime.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_weread_integration_auth_login_locates_real_qr(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)
        try:
            output = await skill.execute("auth_login", {"platform": "weread", "__session_id": "integration"})

            assert output.status == "success"
            assert len(output.attachments) == 1
            assert Path(output.attachments[0].url).exists() is True
        finally:
            await skill.playwright_runtime.shutdown()

    asyncio.run(_run())
