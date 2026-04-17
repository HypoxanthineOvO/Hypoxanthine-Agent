from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.auth_skill import AuthSkill


class StubWeWeClient:
    def __init__(
        self,
        *,
        create_login_payload: dict[str, Any] | None = None,
        login_payload: dict[str, Any] | None = None,
        accounts_payload: dict[str, Any] | None = None,
    ) -> None:
        self.create_login_payload = dict(
            create_login_payload or {"uuid": "uuid-1", "scanUrl": "https://scan.example/wewe"}
        )
        self.login_payload = dict(login_payload or {})
        self.accounts_payload = dict(accounts_payload or {"items": []})
        self.create_login_calls = 0
        self.login_result_calls: list[str] = []
        self.added_accounts: list[dict[str, str]] = []

    async def create_login_url(self) -> dict[str, Any]:
        self.create_login_calls += 1
        return dict(self.create_login_payload)

    async def get_login_result(self, login_id: str) -> dict[str, Any]:
        self.login_result_calls.append(login_id)
        return dict(self.login_payload)

    async def add_account(self, *, id: str, name: str, token: str) -> dict[str, Any]:
        self.added_accounts.append({"id": id, "name": name, "token": token})
        return {"ok": True}

    async def list_accounts(self) -> dict[str, Any]:
        return dict(self.accounts_payload)

    async def close(self) -> None:
        return None


class StubSubscriptionManager:
    def __init__(self) -> None:
        self.refresh_fetchers_calls = 0
        self.refresh_resolver_calls = 0
        self.fetchers: dict[str, Any] = {}
        self.target_resolver: Any = None

    def _build_default_fetchers(self) -> dict[str, Any]:
        self.refresh_fetchers_calls += 1
        return {"rebuilt": self.refresh_fetchers_calls}

    def _build_target_resolver(self) -> str:
        self.refresh_resolver_calls += 1
        return f"resolver-{self.refresh_resolver_calls}"


def _write_secrets(path: Path) -> None:
    payload = {
        "providers": {"openai": {"api_key": "test-key", "api_base": "https://example.invalid/v1"}},
        "services": {
            "bilibili": {"cookie": "SESSDATA=old; DedeUserID=1; bili_jct=oldcsrf"},
            "weibo": {"cookie": "SUB=demo; SUBP=demo2"},
            "zhihu": {"cookie": "z_c0=demo"},
            "wewe_rss": {"enabled": True, "base_url": "https://wewe.example", "auth_code": "auth-demo"},
            "hypo_info": {"base_url": "https://info.example"},
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _build_skill(
    tmp_path: Path,
    *,
    transport: httpx.MockTransport | None = None,
    wewe_client: StubWeWeClient | None = None,
    subscription_manager: StubSubscriptionManager | None = None,
    auth_check_poll_attempts: int = 4,
    sleep_func: Any | None = None,
) -> AuthSkill:
    secrets_path = tmp_path / "secrets.yaml"
    if not secrets_path.exists():
        _write_secrets(secrets_path)
    return AuthSkill(
        structured_store=StructuredStore(tmp_path / "hypo.db"),
        secrets_path=secrets_path,
        qr_dir=tmp_path / "auth-qr",
        http_transport=transport,
        wewe_client_factory=(lambda _cfg: wewe_client or StubWeWeClient()),
        subscription_manager=subscription_manager,
        auth_check_poll_attempts=auth_check_poll_attempts,
        auth_check_poll_interval_seconds=0,
        sleep_func=sleep_func,
    )


async def _noop_sleep(_: float) -> None:
    return None


def test_check_not_login(tmp_path: Path) -> None:
    skill = _build_skill(tmp_path)
    tools = {item["function"]["name"]: item["function"]["description"] for item in skill.tools}

    assert "不要在用户说'扫了/登录好了'时调用" in tools["auth_login"]
    assert "用户说'扫了/扫码完成/登录好了/搞定了'时调用此工具" in tools["auth_check"]
    assert "不要调用 auth_login" in tools["auth_check"]


def test_bilibili_login_generates_real_qr_url(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/x/passport-login/web/qrcode/generate"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "url": "https://passport.bilibili.com/h5-app/passport/login/scan",
                    "qrcode_key": "qr-key-1",
                },
            },
        )

    async def _run() -> None:
        skill = _build_skill(tmp_path, transport=httpx.MockTransport(_handler))

        output = await skill.execute("auth_login", {"platform": "bilibili", "__session_id": "s1"})

        assert output.status == "success"
        assert len(output.attachments) == 1
        assert output.attachments[0].type == "image"
        assert Path(output.attachments[0].url).exists() is True
        assert "扫描" in str(output.result)

    asyncio.run(_run())


def test_bilibili_check_updates_secrets(tmp_path: Path) -> None:
    subscription_manager = StubSubscriptionManager()

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "url": "https://passport.bilibili.com/h5-app/passport/login/scan",
                        "qrcode_key": "qr-key-1",
                    },
                },
            )
        assert request.url.path == "/x/passport-login/web/qrcode/poll"
        assert request.url.params["qrcode_key"] == "qr-key-1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "code": 0,
                    "url": (
                        "https://www.bilibili.com/?SESSDATA=newsess"
                        "&DedeUserID=233"
                        "&bili_jct=newcsrf"
                    ),
                },
            },
        )

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            subscription_manager=subscription_manager,
        )

        await skill.execute("auth_login", {"platform": "bilibili", "__session_id": "s1"})
        output = await skill.execute("auth_check", {"platform": "bilibili", "__session_id": "s1"})

        assert output.status == "success"
        assert "登录成功" in str(output.result)
        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert saved["services"]["bilibili"]["cookie"] == "SESSDATA=newsess; DedeUserID=233; bili_jct=newcsrf"
        assert saved["services"]["weibo"]["cookie"] == "SUB=demo; SUBP=demo2"
        assert saved["services"]["hypo_info"]["base_url"] == "https://info.example"
        assert subscription_manager.refresh_fetchers_calls == 1
        assert subscription_manager.refresh_resolver_calls == 1

    asyncio.run(_run())


def test_auth_check_bilibili_not_scanned(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"url": "https://scan.example", "qrcode_key": "qr-key-1"}},
            )
        return httpx.Response(200, json={"code": 0, "data": {"code": 86101}})

    async def _run() -> None:
        skill = _build_skill(tmp_path, transport=httpx.MockTransport(_handler))

        await skill.execute("auth_login", {"platform": "bilibili"})
        output = await skill.execute("auth_check", {"platform": "bilibili"})

        assert output.status == "success"
        assert "还没有扫码" in str(output.result)

    asyncio.run(_run())


def test_auth_check_bilibili_expired(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"url": "https://scan.example", "qrcode_key": "qr-key-1"}},
            )
        return httpx.Response(200, json={"code": 0, "data": {"code": 86038}})

    async def _run() -> None:
        skill = _build_skill(tmp_path, transport=httpx.MockTransport(_handler))

        await skill.execute("auth_login", {"platform": "bilibili"})
        output = await skill.execute("auth_check", {"platform": "bilibili"})

        assert output.status == "success"
        assert "二维码已过期" in str(output.result)

    asyncio.run(_run())


def test_check_polls_multiple_times(tmp_path: Path) -> None:
    poll_codes = [86090, 86090, 0]
    seen_calls: list[int] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"url": "https://scan.example", "qrcode_key": "qr-key-1"},
                },
            )
        seen_calls.append(len(seen_calls) + 1)
        poll_code = poll_codes.pop(0)
        if poll_code == 0:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "code": 0,
                        "url": (
                            "https://www.bilibili.com/?SESSDATA=finalsess"
                            "&DedeUserID=8"
                            "&bili_jct=csrf8"
                        ),
                    },
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {"code": poll_code}})

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            auth_check_poll_attempts=5,
            sleep_func=_noop_sleep,
        )

        await skill.execute("auth_login", {"platform": "bilibili", "__session_id": "s1"})
        output = await skill.execute("auth_check", {"platform": "bilibili", "__session_id": "s1"})

        assert output.status == "success"
        assert "登录成功" in str(output.result)
        assert len(seen_calls) >= 3

    asyncio.run(_run())


def test_wewe_login_not_unsupported(tmp_path: Path) -> None:
    wewe_client = StubWeWeClient()

    async def _run() -> None:
        skill = _build_skill(tmp_path, wewe_client=wewe_client)

        output = await skill.execute("auth_login", {"platform": "wewe_rss", "__session_id": "s1"})

        assert output.status == "success"
        assert "暂不支持" not in str(output.result)
        assert "扫码登录" in str(output.result)
        assert len(output.attachments) == 1
        assert Path(output.attachments[0].url).exists() is True
        assert wewe_client.create_login_calls == 1
        pending = await skill._load_pending("wewe_rss")
        assert pending is not None
        assert pending["login_id"] == "uuid-1"

    asyncio.run(_run())


def test_wewe_check_if_applicable(tmp_path: Path) -> None:
    wewe_client = StubWeWeClient(
        login_payload={"vid": "vid-1", "username": "reader-a", "token": "token-1"},
    )

    async def _run() -> None:
        skill = _build_skill(tmp_path, wewe_client=wewe_client)

        await skill.execute("auth_login", {"platform": "wewe_rss", "__session_id": "s1"})
        output = await skill.execute("auth_check", {"platform": "wewe_rss", "__session_id": "s1"})

        assert output.status == "success"
        assert "登录成功" in str(output.result)
        assert wewe_client.login_result_calls == ["uuid-1"]
        assert wewe_client.added_accounts == [{"id": "vid-1", "name": "reader-a", "token": "token-1"}]
        pending = await skill._load_pending("wewe_rss")
        assert pending is None

    asyncio.run(_run())


def test_set_cookie_updates_secrets(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        output = await skill.execute("auth_set_cookie", {"platform": "weibo", "cookie": "SUB=xxx; SUBP=yyy"})

        assert output.status == "success"
        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert saved["services"]["weibo"]["cookie"] == "SUB=xxx; SUBP=yyy"
        assert saved["services"]["hypo_info"]["base_url"] == "https://info.example"

    asyncio.run(_run())


def test_set_cookie_refreshes_subscription_manager(tmp_path: Path) -> None:
    subscription_manager = StubSubscriptionManager()

    async def _run() -> None:
        skill = _build_skill(tmp_path, subscription_manager=subscription_manager)

        output = await skill.execute("auth_set_cookie", {"platform": "zhihu", "cookie": "z_c0=updated"})

        assert output.status == "success"
        assert subscription_manager.refresh_fetchers_calls == 1
        assert subscription_manager.refresh_resolver_calls == 1

    asyncio.run(_run())


def test_set_cookie_empty_rejected(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        output = await skill.execute("auth_set_cookie", {"platform": "weibo", "cookie": ""})

        assert output.status == "error"
        assert "cookie is required" in str(output.error_info)

    asyncio.run(_run())


def test_set_cookie_invalid_platform(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        output = await skill.execute("auth_set_cookie", {"platform": "wewe_rss", "cookie": "token=abc"})

        assert output.status == "error"
        assert "不支持手动导入 Cookie" in str(output.error_info)

    asyncio.run(_run())


def test_auth_status_shows_all_platforms(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.bilibili.com/x/web-interface/nav" in url:
            return httpx.Response(200, json={"code": 0, "data": {"isLogin": True, "uname": "快乐的次黄嘌呤"}})
        if "m.weibo.cn/api/config" in url:
            return httpx.Response(200, json={"ok": -100, "msg": "登录失效", "data": {"login": False}})
        if "www.zhihu.com/api/v4/me" in url:
            return httpx.Response(200, json={"name": "知乎用户A"})
        raise AssertionError(f"unexpected request: {request.url}")

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            wewe_client=StubWeWeClient(accounts_payload={"items": [{"id": "vid-1", "name": "reader-a", "status": 1}]}),
        )

        output = await skill.execute("auth_status", {})

        assert output.status == "success"
        text = str(output.result)
        assert "B站: ✅ 有效（用户: 快乐的次黄嘌呤）" in text
        assert "微博: ❌ 已失效" in text
        assert "知乎: ✅ 有效（用户: 知乎用户A）" in text
        assert "WeWe RSS: ✅ 已配置" in text

    asyncio.run(_run())


def test_auth_verify_weibo_returns_cookie_guidance(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        output = await skill.execute("auth_verify", {"platform": "weibo", "code": "123456"})

        assert output.status == "success"
        text = str(output.result)
        assert "当前走扫码登录" in text
        assert "auth_check" in text
        assert "暂不支持" not in text

    asyncio.run(_run())


def test_no_unsupported_for_configured_platforms(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "url": "https://passport.bilibili.com/h5-app/passport/login/scan",
                        "qrcode_key": "qr-key-1",
                    },
                },
            )
        if request.url.path == "/sso/v2/qrcode/image":
            return httpx.Response(
                200,
                json={
                    "retcode": 20000000,
                    "data": {
                        "qrid": "weibo-qr-1",
                        "image": "https://example.com/weibo_qr",
                    },
                },
            )
        if request.url.path == "/udid":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/v3/account/api/login/qrcode":
            return httpx.Response(
                200,
                json={
                    "token": "zhihu-token-1",
                    "link": "https://www.zhihu.com/account/scan/login/token-1",
                    "expires_at": 1893456000,
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    wewe_client = StubWeWeClient()

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            wewe_client=wewe_client,
        )
        payload = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        services = payload["services"]

        for platform in ("bilibili", "weibo", "zhihu", "wewe_rss"):
            assert platform in services
            output = await skill.execute("auth_login", {"platform": platform, "__session_id": "s1"})
            assert output.status == "success"
            assert "暂不支持" not in str(output.result)

    asyncio.run(_run())


def test_auth_revoke(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        output = await skill.execute("auth_revoke", {"platform": "bilibili"})

        assert output.status == "success"
        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert "cookie" not in saved["services"]["bilibili"]

    asyncio.run(_run())


def test_cookie_update_preserves_other_fields(tmp_path: Path) -> None:
    async def _run() -> None:
        skill = _build_skill(tmp_path)

        await skill._update_cookie("bilibili", "SESSDATA=updated; DedeUserID=9; bili_jct=csrf")

        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert saved["providers"]["openai"]["api_key"] == "test-key"
        assert saved["services"]["weibo"]["cookie"] == "SUB=demo; SUBP=demo2"
        assert saved["services"]["wewe_rss"]["auth_code"] == "auth-demo"
        assert saved["services"]["bilibili"]["cookie"] == "SESSDATA=updated; DedeUserID=9; bili_jct=csrf"

    asyncio.run(_run())


def test_wewe_pending_state_persists_in_store(tmp_path: Path) -> None:
    wewe_client = StubWeWeClient()

    async def _run() -> None:
        skill = _build_skill(tmp_path, wewe_client=wewe_client)

        await skill.execute("auth_login", {"platform": "wewe_rss", "__session_id": "s1"})

        await skill._ensure_store_ready()
        raw = await skill.structured_store.get_preference("auth.pending.wewe_rss")
        assert raw is not None
        payload = json.loads(raw)
        assert payload["login_id"] == "uuid-1"
        assert payload["session_id"] == "s1"

    asyncio.run(_run())
