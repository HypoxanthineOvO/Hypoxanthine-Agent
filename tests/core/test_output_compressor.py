from __future__ import annotations

import asyncio
import json
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


def _tool_output(stdout: str, *, stderr: str = "", exit_code: int = 0) -> str:
    return json.dumps(
        {
            "status": "success" if exit_code == 0 else "error",
            "result": {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
            },
            "metadata": {},
            "error_info": "" if exit_code == 0 else f"Command exited with status {exit_code}",
        },
        ensure_ascii=False,
    )


def test_output_compressor_passthrough_under_threshold() -> None:
    router = StubRouter(lambda **_: "unused")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed("ok", metadata={})
        assert output == "ok"
        assert compressed is False
        assert router.calls == []

    asyncio.run(_run())


def test_output_compressor_keeps_moderate_structured_tool_output() -> None:
    router = StubRouter(lambda **_: "summary text")
    compressor = OutputCompressor(router=router)
    stdout = "\n".join(
        f"user{i:03d} {('python worker ' * 20).strip()}"
        for i in range(90)
    )
    large = _tool_output(stdout)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            large,
            metadata={"session_id": "s1"},
        )
        assert len(large) > compressor.threshold_chars
        assert compressed is False
        assert output == large
        assert router.calls == []

    asyncio.run(_run())


def test_output_compressor_single_pass_for_line_heavy_output() -> None:
    router = StubRouter(lambda **_: "summary text")
    compressor = OutputCompressor(router=router)
    stdout = "\n".join(
        f"2026-03-17T00:{i % 60:02d}:00Z worker[{i:04d}] {'x' * 60}"
        for i in range(420)
    )
    large = _tool_output(stdout)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            large,
            metadata={"session_id": "s1"},
        )
        assert len(large) > compressor.threshold_chars
        assert compressed is True
        assert len(output) <= compressor.target_chars
        assert "summary text" in output
        assert len(router.calls) == 1

    asyncio.run(_run())


def test_output_compressor_chunked_compression_for_very_large_output() -> None:
    router = StubRouter(lambda **_: "chunk summary")
    compressor = OutputCompressor(router=router)
    huge = _tool_output("b" * 170000)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            huge,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert len(output) <= compressor.target_chars
        assert len(router.calls) >= 2

    asyncio.run(_run())


def test_output_compressor_stops_after_max_iterations() -> None:
    router = StubRouter(lambda **_: "z" * 4000)
    compressor = OutputCompressor(
        router=router,
        threshold_chars=2500,
        target_chars=2500,
        hard_threshold_chars=2500,
        structured_passthrough_chars=2500,
        max_chunk_chars=80000,
        chunk_model_limit_chars=10000,
        max_iterations=3,
    )
    large = _tool_output("c" * 3000)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            large,
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert len(router.calls) == 3
        assert len(output) <= compressor.target_chars

    asyncio.run(_run())


def test_output_compressor_keeps_recent_original_cache_of_ten_entries() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        for i in range(11):
            await compressor.compress_if_needed(
                _tool_output("x" * 110000 + str(i)),
                metadata={"session_id": "s1", "index": i},
            )

        cache = compressor.get_recent_originals()
        assert len(cache) == 10
        originals = [item["output"] for item in cache.values()]
        assert _tool_output("x" * 110000 + "0") not in originals

    asyncio.run(_run())


def test_compression_marker_appended() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            _tool_output("y" * 110000),
            metadata={"session_id": "s1"},
        )
        assert compressed is True
        assert re.search(
            r"\n\[📦 Output compressed from \d+ → \d+ chars\. Original saved to logs\. Ask me for details\.\]$",
            output,
        )

    asyncio.run(_run())


def test_output_compressor_writes_compressed_meta_to_input_metadata() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        metadata: dict[str, object] = {"session_id": "s1", "tool_name": "exec_command"}
        original_output = _tool_output("z" * 110000)
        output, compressed = await compressor.compress_if_needed(
            original_output,
            metadata=metadata,
        )

        assert compressed is True
        assert len(output) <= compressor.target_chars
        compressed_meta = metadata.get("compressed_meta")
        assert isinstance(compressed_meta, dict)
        assert set(compressed_meta.keys()) == {
            "cache_id",
            "original_chars",
            "compressed_chars",
        }
        assert int(compressed_meta["original_chars"]) == len(original_output)
        assert int(compressed_meta["compressed_chars"]) == len(output)
        cache_id = str(compressed_meta["cache_id"])
        assert compressor.get_original_output(cache_id) == original_output

    asyncio.run(_run())
