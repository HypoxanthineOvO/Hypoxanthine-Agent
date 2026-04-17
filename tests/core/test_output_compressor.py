from __future__ import annotations

import asyncio

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


def test_short_output_not_compressed() -> None:
    router = StubRouter(lambda **_: "unused")
    compressor = OutputCompressor(router=router)
    short_output = "a" * 15000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(short_output, metadata={})
        assert compressed is False
        assert output == short_output
        assert router.calls == []

    asyncio.run(_run())


def test_long_output_compressed() -> None:
    router = StubRouter(lambda **_: "summary line\nimportant details")
    compressor = OutputCompressor(router=router)
    long_output = "b" * 30000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            long_output,
            metadata={"session_id": "s1"},
            tool_name="run_command",
        )
        assert compressed is True
        assert len(output) <= 8000
        assert "summary line" in output
        assert router.calls

    asyncio.run(_run())


def test_very_long_truncated() -> None:
    router = StubRouter(lambda **_: "should not be called")
    compressor = OutputCompressor(router=router)
    huge_output = "c" * 80000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            huge_output,
            metadata={"session_id": "s1"},
            tool_name="run_command",
        )
        assert compressed is True
        assert router.calls == []
        assert len(output) < len(huge_output)
        assert len(output) <= 64000
        assert "输出过长已截断" in output
        assert output.startswith("c" * 60000)

    asyncio.run(_run())


def test_strategy_selection_web_search() -> None:
    router = StubRouter(lambda **_: "compressed")
    compressor = OutputCompressor(router=router)
    long_output = "search result\n" * 3000

    async def _run() -> None:
        await compressor.compress_if_needed(
            long_output,
            metadata={"session_id": "s1"},
            tool_name="web_search",
        )
        prompt = str(router.calls[-1]["messages"][0]["content"])
        assert "保留每条搜索结果的标题、URL和关键摘要句" in prompt

    asyncio.run(_run())


def test_strategy_selection_default() -> None:
    router = StubRouter(lambda **_: "compressed")
    compressor = OutputCompressor(router=router)
    long_output = "generic output\n" * 3000

    async def _run() -> None:
        await compressor.compress_if_needed(
            long_output,
            metadata={"session_id": "s1"},
        )
        prompt = str(router.calls[-1]["messages"][0]["content"])
        assert "保留关键数据点、具体数值、文件名、URL" in prompt

    asyncio.run(_run())


def test_compression_preserves_reference() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)
    long_output = "d" * 30000

    async def _run() -> None:
        output, compressed = await compressor.compress_if_needed(
            long_output,
            metadata={"session_id": "s1", "tool_name": "web_read"},
            tool_name="web_read",
        )
        assert compressed is True
        assert '[📦 原始输出 ' in output
        assert output.endswith('如需查看原文请说"给我看原始输出"]')

    asyncio.run(_run())


def test_output_compressor_keeps_recent_original_cache_of_ten_entries() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        for i in range(11):
            await compressor.compress_if_needed(
                ("x" * 30000) + str(i),
                metadata={"session_id": "s1", "index": i},
                tool_name="run_command",
            )

        cache = compressor.get_recent_originals()
        assert len(cache) == 10
        originals = [item["output"] for item in cache.values()]
        assert ("x" * 30000) + "0" not in originals

    asyncio.run(_run())


def test_output_compressor_writes_compressed_meta_to_input_metadata() -> None:
    router = StubRouter(lambda **_: "summary")
    compressor = OutputCompressor(router=router)

    async def _run() -> None:
        metadata: dict[str, object] = {"session_id": "s1", "tool_name": "run_command"}
        original_output = "z" * 30000
        output, compressed = await compressor.compress_if_needed(
            original_output,
            metadata=metadata,
            tool_name="run_command",
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
