from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hypo_agent.core.wewe_rss_monitor import WeWeRSSMonitorService
from hypo_agent.memory.structured_store import StructuredStore


class StubEventQueue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class StubClient:
    def __init__(
        self,
        *,
        accounts_payload: dict[str, Any] | None = None,
        login_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.accounts_payload = accounts_payload or {"items": [], "blocks": []}
        self.login_payloads = list(login_payloads or [])
        self.added_accounts: list[dict[str, str]] = []

    async def list_accounts(self) -> dict[str, Any]:
        return self.accounts_payload

    async def create_login_url(self) -> dict[str, Any]:
        return {"uuid": "uuid-1", "scanUrl": "https://scan.example/qr-1"}

    async def get_login_result(self, login_id: str) -> dict[str, Any]:
        assert login_id == "uuid-1"
        if self.login_payloads:
            return self.login_payloads.pop(0)
        return {}

    async def add_account(self, *, id: str, name: str, token: str) -> dict[str, Any]:
        self.added_accounts.append({"id": id, "name": name, "token": token})
        return {"ok": True}


async def _noop_sleep(_: float) -> None:
    return None


def test_wewe_rss_monitor_enqueues_alert_for_invalid_accounts(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = StubEventQueue()
        store = StructuredStore(db_path=tmp_path / "agent.db")
        service = WeWeRSSMonitorService(
            client=StubClient(
                accounts_payload={
                    "items": [
                        {"id": "vid-1", "name": "reader-a", "status": 0},
                        {"id": "vid-2", "name": "reader-b", "status": 1},
                    ],
                    "blocks": [],
                }
            ),
            structured_store=store,
            event_queue=queue,
            qr_dir=tmp_path / "qr",
            sleep_func=_noop_sleep,
        )

        result = await service.check_accounts()
        assert result["status"] == "alerted"
        assert len(queue.events) == 1
        assert queue.events[0]["event_type"] == "wewe_rss_trigger"
        assert "reader-a" in queue.events[0]["summary"]

        repeated = await service.check_accounts()
        assert repeated["status"] == "deduped"
        assert len(queue.events) == 1

    asyncio.run(_run())


def test_wewe_rss_monitor_clears_alert_signature_when_accounts_recover(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = StubEventQueue()
        store = StructuredStore(db_path=tmp_path / "agent.db")
        client = StubClient(
            accounts_payload={"items": [{"id": "vid-1", "name": "reader-a", "status": 0}], "blocks": []}
        )
        service = WeWeRSSMonitorService(
            client=client,
            structured_store=store,
            event_queue=queue,
            qr_dir=tmp_path / "qr",
            sleep_func=_noop_sleep,
        )

        await service.check_accounts()
        assert await store.get_preference("wewe_rss.last_alert_signature") is not None

        client.accounts_payload = {"items": [{"id": "vid-1", "name": "reader-a", "status": 1}], "blocks": []}
        result = await service.check_accounts()
        assert result["status"] == "healthy"
        assert await store.get_preference("wewe_rss.last_alert_signature") is None

    asyncio.run(_run())


def test_wewe_rss_monitor_start_login_flow_returns_qr_message_and_polls_success(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = StubEventQueue()
        store = StructuredStore(db_path=tmp_path / "agent.db")
        client = StubClient(
            login_payloads=[
                {},
                {"vid": "vid-9", "username": "reader-z", "token": "token-z"},
            ]
        )
        service = WeWeRSSMonitorService(
            client=client,
            structured_store=store,
            event_queue=queue,
            qr_dir=tmp_path / "qr",
            sleep_func=_noop_sleep,
            login_timeout_seconds=5,
            poll_interval_seconds=1,
        )

        message = await service.start_login_flow(
            session_id="main",
            channel="qq",
            sender_id="user-1",
        )
        assert "扫码" in str(message.text)
        assert message.metadata["target_channels"] == ["qq"]
        assert len(message.attachments) == 1
        assert message.attachments[0].type == "image"
        assert Path(message.attachments[0].url).exists() is True

        await service.wait_for_background_tasks()

        assert client.added_accounts == [{"id": "vid-9", "name": "reader-z", "token": "token-z"}]
        assert len(queue.events) == 1
        assert queue.events[0]["channel"] == "qq"
        assert "已恢复" in queue.events[0]["summary"]

    asyncio.run(_run())


def test_wewe_rss_monitor_login_timeout_enqueues_failure(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = StubEventQueue()
        store = StructuredStore(db_path=tmp_path / "agent.db")
        service = WeWeRSSMonitorService(
            client=StubClient(login_payloads=[{}, {}, {}]),
            structured_store=store,
            event_queue=queue,
            qr_dir=tmp_path / "qr",
            sleep_func=_noop_sleep,
            login_timeout_seconds=2,
            poll_interval_seconds=1,
        )

        await service.start_login_flow(
            session_id="main",
            channel="weixin",
            sender_id="user-1",
        )
        await service.wait_for_background_tasks()

        assert len(queue.events) == 1
        assert queue.events[0]["channel"] == "weixin"
        assert "超时" in queue.events[0]["summary"]

    asyncio.run(_run())
