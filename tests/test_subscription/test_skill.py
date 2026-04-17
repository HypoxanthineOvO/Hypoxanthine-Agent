from __future__ import annotations

import asyncio
from typing import Any

from hypo_agent.skills.subscription.skill import SubscriptionSkill


class StubManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.target_resolver = None

    async def add_subscription(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("add", dict(kwargs)))
        return {"id": "sub-1", "name": kwargs["name"], "platform": kwargs["platform"]}

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        self.calls.append(("list", {}))
        return [{"id": "sub-1", "name": "author-demo-video", "enabled": True}]

    async def remove_subscription(self, subscription_id: str) -> bool:
        self.calls.append(("remove", {"subscription_id": subscription_id}))
        return True

    async def check_subscriptions(self, subscription_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("check", {"subscription_id": subscription_id}))
        return {"checked": 1, "new_items": 0}

    async def get_status(self) -> dict[str, Any]:
        self.calls.append(("status", {}))
        return {"total": 1, "active": 1}


def test_subscription_skill_exposes_six_tools() -> None:
    skill = SubscriptionSkill(manager=StubManager())

    assert [tool["function"]["name"] for tool in skill.tools] == [
        "sub_add",
        "sub_search",
        "sub_list",
        "sub_remove",
        "sub_check",
        "sub_status",
    ]


def test_subscription_skill_dispatches_tools_to_manager() -> None:
    async def _run() -> None:
        manager = StubManager()
        skill = SubscriptionSkill(manager=manager)

        added = await skill.execute(
            "sub_add",
            {
                "platform": "bilibili",
                "target_id": "546195",
                "name": "author-demo-video",
                "fetcher_key": "bilibili_video",
                "interval_minutes": 10,
            },
        )
        listed = await skill.execute("sub_list", {})
        removed = await skill.execute("sub_remove", {"subscription_id": "sub-1"})
        checked = await skill.execute("sub_check", {"subscription_id": "sub-1"})
        status = await skill.execute("sub_status", {})

        assert added.status == "success"
        assert listed.result["items"][0]["name"] == "author-demo-video"
        assert removed.result["removed"] is True
        assert checked.result["checked"] == 1
        assert status.result["active"] == 1
        assert [name for name, _ in manager.calls] == ["add", "list", "remove", "check", "status"]

    asyncio.run(_run())


def test_subscription_skill_sub_check_treats_all_alias_as_global_check() -> None:
    async def _run() -> None:
        manager = StubManager()
        skill = SubscriptionSkill(manager=manager)

        checked = await skill.execute("sub_check", {"subscription_id": "all"})

        assert checked.status == "success"
        assert checked.result["checked"] == 1
        assert manager.calls == [("check", {"subscription_id": None})]

    asyncio.run(_run())
