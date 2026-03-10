from __future__ import annotations

import asyncio
import re

from hypo_agent.core.output_compressor import OutputCompressor


class StubRouter:
    def __init__(self, response_fn) -> None:
        self.response_fn = response_fn
        self.calls: list[dict] = []

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "DeepseekV3_2"

    async def call(
        self,
        model_name: str,
        messages: list[dict],
        *,
        session_id: str | None = None,
        tools=None,
    ) -> str:
        del tools
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "session_id": session_id,
            }
        )
        return self.response_fn(model_name=model_name, messages=messages, session_id=session_id)


def test_output_compressor_passthrough_under_threshold() -> None:
    router = StubRouter(lambda **_: "unused")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed("ok", metadata={})
        assert output == "ok"
        assert compressed is False
        assert router.calls == []

    asyncio.run(_run())


def test_output_compressor_single_pass_for_medium_output() -> None:
    router = StubRouter(lambda **_: "summary text")
    compressor = OutputCompressor(router=router)
    large = "a" * 3000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            large,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert len(output) <= 2500
        assert "summary text" in output
        assert len(router.calls) == 1

    asyncio.run(_run())


def test_output_compressor_chunked_compression_for_very_large_output() -> None:
    router = StubRouter(lambda **_: "chunk summary")
    compressor = OutputCompressor(router=router)
    huge = "b" * 170000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            huge,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert len(output) <= 2500
        assert len(router.calls) >= 2

    asyncio.run(_run())


def test_output_compressor_stops_after_max_iterations() -> None:
    router = StubRouter(lambda **_: "z" * 4000)
    compressor = OutputCompressor(
        router=router,
        threshold_chars=2500,
        target_chars=2500,
        max_chunk_chars=80000,
        chunk_model_limit_chars=10000,
        max_iterations=3,
    )
    large = "c" * 3000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            large,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert len(router.calls) == 3
        assert len(output) <= 2500

    asyncio.run(_run())


def test_output_compressor_keeps_recent_original_cache_of_ten_entries() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        for i in range(11):
            await compressor.compress_if_needed(
                "x" * 3000 + str(i),
                metadata={"session_id": "s1", "index": i},
            )

        cache = compressor.get_recent_originals()
        assert len(cache) == 10
        originals = [item["output"] for item in cache.values()]
        assert "x" * 3000 + "0" not in originals

    asyncio.run(_run())


def test_compression_marker_chinese() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            "y" * 3000,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert re.match(
            r"^\[📦 输出已压缩 \(\d+ → \d+ 字符\)。如需查看原文，请告知。\]\n",
            output,
        )

    asyncio.run(_run())


def test_output_compressor_writes_compressed_meta_to_input_metadata() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        metadata: dict[str, object] = {"session_id": "s1", "tool_name": "run_command"}
        output, compressed = await compressor.compress_if_needed(
            "z" * 3000,
            metadata=metadata,
        )

        assert compressed is True
        assert len(output) <= 2500
        compressed_meta = metadata.get("compressed_meta")
        assert isinstance(compressed_meta, dict)
        assert set(compressed_meta.keys()) == {
            "cache_id",
            "original_chars",
            "compressed_chars",
        }
        assert int(compressed_meta["original_chars"]) == 3000
        assert int(compressed_meta["compressed_chars"]) == len(output)
        cache_id = str(compressed_meta["cache_id"])
        assert compressor.get_original_output(cache_id) == "z" * 3000

    asyncio.run(_run())
