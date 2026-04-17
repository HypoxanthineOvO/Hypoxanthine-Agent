from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml

from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.skills.auth_skill import AuthSkill


class StubWeWeClient:
    async def create_login_url(self) -> dict[str, Any]:
        return {"uuid": "uuid-1", "scanUrl": "https://scan.example/wewe"}

    async def get_login_result(self, login_id: str) -> dict[str, Any]:
        del login_id
        return {}

    async def add_account(self, *, id: str, name: str, token: str) -> dict[str, Any]:
        del id, name, token
        return {"ok": True}

    async def list_accounts(self) -> dict[str, Any]:
        return {"items": []}

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


class FakePage:
    def __init__(self, *, cookie_name: str = "wr_skey", cookie_value: str = "cookie-1") -> None:
        self.cookie_name = cookie_name
        self.cookie_value = cookie_value
        self.goto_calls: list[str] = []
        self.clicked_texts: list[str] = []
        self.screenshot_paths: list[str] = []
        self.url = "https://weread.qq.com/"
        self.title_text = "微信读书"

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> None:
        del wait_until, timeout
        self.goto_calls.append(url)

    async def wait_for_timeout(self, ms: int) -> None:
        del ms

    async def title(self) -> str:
        return self.title_text

    def get_by_text(self, text: str, exact: bool = False) -> "FakeLocator":
        del exact
        return FakeLocator(page=self, kind="text", value=text)

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(page=self, kind="selector", value=selector)


class FakeLocator:
    def __init__(self, *, page: FakePage, kind: str, value: str) -> None:
        self.page = page
        self.kind = kind
        self.value = value

    async def click(self, timeout: int = 10000) -> None:
        del timeout
        if self.kind == "text":
            self.page.clicked_texts.append(self.value)

    async def wait_for(self, state: str = "visible", timeout: int = 30000) -> None:
        del state, timeout

    async def screenshot(self, path: str) -> None:
        self.page.screenshot_paths.append(path)
        Path(path).write_bytes(b"png")


class FakeBrowserContext:
    def __init__(self, *, page: FakePage | None = None) -> None:
        self.page = page or FakePage()
        self.closed = False
        self.cookie_calls = 0

    async def new_page(self) -> FakePage:
        return self.page

    async def cookies(self) -> list[dict[str, Any]]:
        self.cookie_calls += 1
        return [
            {
                "name": self.page.cookie_name,
                "value": self.page.cookie_value,
                "domain": ".weread.qq.com",
                "path": "/",
            }
        ]

    async def close(self) -> None:
        self.closed = True


class FakePlaywrightRuntime:
    def __init__(self, *, context: FakeBrowserContext | None = None, available: bool = True) -> None:
        self.context = context or FakeBrowserContext()
        self.available = available
        self.contexts: dict[str, FakeBrowserContext] = {}
        self.new_context_calls = 0
        self.cleaned_context_ids: list[str] = []

    async def initialize(self) -> None:
        return None

    async def new_context(self) -> tuple[str, FakeBrowserContext]:
        self.new_context_calls += 1
        context_id = f"context-{self.new_context_calls}"
        self.contexts[context_id] = self.context
        return context_id, self.context

    async def close_context(self, context_id: str) -> None:
        self.cleaned_context_ids.append(context_id)
        context = self.contexts.pop(context_id, None)
        if context is not None:
            await context.close()


async def _noop_sleep(_: float) -> None:
    return None


def _write_secrets(path: Path) -> None:
    payload = {
        "providers": {"openai": {"api_key": "test-key", "api_base": "https://example.invalid/v1"}},
        "services": {
            "bilibili": {"cookie": "SESSDATA=old; DedeUserID=1; bili_jct=oldcsrf"},
            "weibo": {"cookie": "SUB=demo; SUBP=demo2"},
            "zhihu": {"cookie": "z_c0=demo"},
            "wewe_rss": {"enabled": True, "base_url": "https://wewe.example", "auth_code": "auth-demo"},
            "weread": {"cookie": ""},
            "hypo_info": {"base_url": "https://info.example"},
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _build_skill(
    tmp_path: Path,
    *,
    transport: httpx.MockTransport | None = None,
    subscription_manager: StubSubscriptionManager | None = None,
    playwright_runtime: FakePlaywrightRuntime | None = None,
) -> AuthSkill:
    secrets_path = tmp_path / "secrets.yaml"
    if not secrets_path.exists():
        _write_secrets(secrets_path)
    return AuthSkill(
        structured_store=StructuredStore(tmp_path / "hypo.db"),
        secrets_path=secrets_path,
        qr_dir=tmp_path / "auth-qr",
        http_transport=transport,
        wewe_client_factory=lambda _cfg: StubWeWeClient(),
        subscription_manager=subscription_manager,
        auth_check_poll_attempts=2,
        auth_check_poll_interval_seconds=0,
        sleep_func=_noop_sleep,
        playwright_runtime=playwright_runtime,
    )


def test_weibo_login_and_check_update_cookie_without_manual_guidance(tmp_path: Path) -> None:
    requests_seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/sso/v2/qrcode/image":
            return httpx.Response(
                200,
                json={
                    "retcode": 20000000,
                    "data": {
                        "qrid": "weibo-qrid",
                        "image": "https://v2.qr.weibo.cn/inf/gen?code=demo",
                    },
                },
            )
        if request.url.path == "/sso/v2/qrcode/check":
            return httpx.Response(
                200,
                json={
                    "retcode": 20000000,
                    "data": {"alt": "weibo-alt", "status": "3"},
                },
            )
        if request.url.path == "/sso/v2/login":
            response = httpx.Response(
                200,
                json={"retcode": 0, "crossDomainUrlList": []},
            )
            response.headers["set-cookie"] = "SUB=sub-new; Domain=.weibo.com; Path=/, SUBP=subp-new; Domain=.weibo.com; Path=/"
            return response
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    subscription_manager = StubSubscriptionManager()

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            subscription_manager=subscription_manager,
        )

        login = await skill.execute("auth_login", {"platform": "weibo", "__session_id": "s1"})
        assert login.status == "success"
        assert "暂不支持" not in str(login.result)
        assert "手动复制 Cookie" not in str(login.result)
        assert len(login.attachments) == 1

        checked = await skill.execute("auth_check", {"platform": "weibo", "__session_id": "s1"})
        assert checked.status == "success"
        assert "登录成功" in str(checked.result)

        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert "SUB=sub-new" in saved["services"]["weibo"]["cookie"]
        assert "SUBP=subp-new" in saved["services"]["weibo"]["cookie"]
        assert subscription_manager.refresh_fetchers_calls == 1
        assert subscription_manager.refresh_resolver_calls == 1
        assert requests_seen == [
            "GET /sso/v2/qrcode/image",
            "GET /sso/v2/qrcode/check",
            "GET /sso/v2/login",
        ]

    asyncio.run(_run())


def test_zhihu_check_falls_back_to_playwright_and_returns_new_qr_attachment(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/udid":
            return httpx.Response(200, text="ok", headers={"set-cookie": "_xsrf=xsrf-demo; Path=/; Domain=.zhihu.com"})
        if request.url.path == "/api/v3/account/api/login/qrcode":
            return httpx.Response(
                200,
                json={
                    "token": "zhihu-token-1",
                    "link": "https://www.zhihu.com/account/scan/login/token-1",
                    "expires_at": 1893456000,
                },
            )
        if request.url.path == "/api/v3/account/api/login/qrcode/zhihu-token-1/scan_info":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": 40352,
                        "message": "系统监测到您的网络环境存在异常",
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def _run() -> None:
        runtime = FakePlaywrightRuntime()
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            playwright_runtime=runtime,
        )

        login = await skill.execute("auth_login", {"platform": "zhihu", "__session_id": "s1"})
        assert login.status == "success"
        assert len(login.attachments) == 1

        checked = await skill.execute("auth_check", {"platform": "zhihu", "__session_id": "s1"})
        assert checked.status == "success"
        assert "自动切换" in str(checked.result)
        assert len(checked.attachments) == 1

        pending = await skill._load_pending("zhihu")
        assert pending is not None
        assert pending["backend_key"] == "playwright"
        assert pending["payload"]["context_id"] == "context-1"
        assert runtime.new_context_calls == 1

    asyncio.run(_run())


def test_weread_login_and_check_use_playwright_and_write_cookie(tmp_path: Path) -> None:
    subscription_manager = StubSubscriptionManager()

    async def _run() -> None:
        runtime = FakePlaywrightRuntime()
        skill = _build_skill(
            tmp_path,
            subscription_manager=subscription_manager,
            playwright_runtime=runtime,
        )

        login = await skill.execute("auth_login", {"platform": "weread", "__session_id": "s1"})
        assert login.status == "success"
        assert "暂不支持" not in str(login.result)
        assert len(login.attachments) == 1

        checked = await skill.execute("auth_check", {"platform": "weread", "__session_id": "s1"})
        assert checked.status == "success"
        assert "登录成功" in str(checked.result)

        saved = yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))
        assert "wr_skey=cookie-1" in saved["services"]["weread"]["cookie"]
        assert subscription_manager.refresh_fetchers_calls == 1
        assert subscription_manager.refresh_resolver_calls == 1
        assert runtime.cleaned_context_ids == ["context-1"]

    asyncio.run(_run())


def test_supported_platforms_do_not_return_not_supported(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/passport-login/web/qrcode/generate":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"url": "https://scan.example", "qrcode_key": "bili-qr"}},
            )
        if request.url.path == "/sso/v2/qrcode/image":
            return httpx.Response(
                200,
                json={"retcode": 20000000, "data": {"qrid": "weibo-qrid", "image": "https://example.com/weibo.png"}},
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
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def _run() -> None:
        skill = _build_skill(
            tmp_path,
            transport=httpx.MockTransport(_handler),
            playwright_runtime=FakePlaywrightRuntime(),
        )

        for platform in ("bilibili", "wewe_rss", "weibo", "zhihu", "weread"):
            output = await skill.execute("auth_login", {"platform": platform, "__session_id": "s1"})
            assert output.status == "success"
            assert "暂不支持" not in str(output.result)
            assert "手动复制 Cookie" not in str(output.result)

    asyncio.run(_run())
