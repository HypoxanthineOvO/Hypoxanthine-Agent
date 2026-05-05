from __future__ import annotations

import litellm

from hypo_agent.core.litellm_runtime import aiohttp_transport_disabled


def test_litellm_runtime_disables_aiohttp_transport() -> None:
    assert aiohttp_transport_disabled() is True
    assert litellm.disable_aiohttp_transport is True
