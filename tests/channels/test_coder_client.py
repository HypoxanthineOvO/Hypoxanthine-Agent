from __future__ import annotations

from hypo_agent.channels.coder.coder_client import CoderClient


def test_coder_client_reports_streaming_unsupported_by_default() -> None:
    client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")

    assert client.supports_streaming() is False


def test_coder_client_reports_continuation_unsupported_by_default() -> None:
    client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")

    assert client.supports_continuation() is False
