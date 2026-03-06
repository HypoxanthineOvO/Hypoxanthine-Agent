from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hypo_agent.core.output_compressor import OutputCompressor
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class StubRouter:
    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "DeepseekV3_2"

    async def call(self, model_name: str, messages: list[dict], *, session_id: str | None = None):
        del model_name, messages, session_id
        return "summary"


def _build_client(tmp_path, *, with_compressor: bool = True) -> tuple[TestClient, OutputCompressor | None]:
    compressor = OutputCompressor(router=StubRouter()) if with_compressor else None
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        output_compressor=compressor,
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app), compressor


def test_get_compressed_original_hit(tmp_path) -> None:
    client, compressor = _build_client(tmp_path)
    assert compressor is not None

    metadata: dict[str, object] = {"session_id": "s1", "tool_name": "run_command"}
    asyncio.run(compressor.compress_if_needed("x" * 3001, metadata=metadata))
    compressed_meta = metadata.get("compressed_meta")
    assert isinstance(compressed_meta, dict)
    cache_id = str(compressed_meta["cache_id"])

    with client:
        response = client.get(
            f"/api/compressed/{cache_id}",
            params={"token": "test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cache_id"] == cache_id
    assert payload["original_output"] == "x" * 3001


def test_get_compressed_original_not_found(tmp_path) -> None:
    client, _ = _build_client(tmp_path)

    with client:
        response = client.get(
            "/api/compressed/not-found",
            params={"token": "test-token"},
        )

    assert response.status_code == 404


def test_get_compressed_original_returns_503_when_unavailable(tmp_path) -> None:
    client, _ = _build_client(tmp_path, with_compressor=False)

    with client:
        response = client.get(
            "/api/compressed/abc",
            params={"token": "test-token"},
        )

    assert response.status_code == 503


def test_get_compressed_original_requires_token(tmp_path) -> None:
    client, _ = _build_client(tmp_path)

    with client:
        response = client.get("/api/compressed/abc")

    assert response.status_code == 401
