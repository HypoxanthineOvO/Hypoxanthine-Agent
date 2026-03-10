from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger("hypo_agent.output_compressor")


class OutputCompressor:
    def __init__(
        self,
        *,
        router: Any,
        threshold_chars: int = 2500,
        target_chars: int = 2500,
        max_chunk_chars: int = 80 * 1024,
        chunk_model_limit_chars: int = 128 * 1024,
        max_iterations: int = 3,
        cache_size: int = 10,
    ) -> None:
        self.router = router
        self.threshold_chars = threshold_chars
        self.target_chars = target_chars
        self.max_chunk_chars = max_chunk_chars
        self.chunk_model_limit_chars = chunk_model_limit_chars
        self.max_iterations = max_iterations
        self.cache_size = cache_size
        self._recent_originals: OrderedDict[str, dict[str, Any]] = OrderedDict()

    async def compress_if_needed(
        self,
        output: str,
        metadata: dict[str, Any],
    ) -> tuple[str, bool]:
        if len(output) <= self.threshold_chars:
            return output, False

        cache_id = self._save_original(output, metadata)
        logger.info(
            "output_compressor.original_saved",
            cache_id=cache_id,
            original_chars=len(output),
            session_id=metadata.get("session_id"),
            tool_name=metadata.get("tool_name"),
            tool_call_id=metadata.get("tool_call_id"),
        )

        working = output
        for _ in range(self.max_iterations):
            if len(working) <= self.target_chars:
                break
            next_output = await self._compress_round(working, metadata)
            if not next_output:
                break
            working = next_output

        marker, body_budget = self._build_marker(len(output), len(working))
        if body_budget <= 0:
            final_output = marker[: self.target_chars]
            self._attach_compressed_meta(
                metadata=metadata,
                cache_id=cache_id,
                original_chars=len(output),
                compressed_chars=len(final_output),
            )
            return final_output, True

        body = working[:body_budget]
        final_output = self._finalize_with_marker(
            original_chars=len(output),
            body=body,
        )
        self._attach_compressed_meta(
            metadata=metadata,
            cache_id=cache_id,
            original_chars=len(output),
            compressed_chars=len(final_output),
        )
        return final_output, True

    def get_recent_originals(self) -> dict[str, dict[str, Any]]:
        return dict(self._recent_originals)

    def get_original_output(self, cache_id: str) -> str | None:
        item = self._recent_originals.get(cache_id)
        if item is None:
            return None
        output = item.get("output")
        return str(output) if isinstance(output, str) else None

    async def _compress_round(self, output: str, metadata: dict[str, Any]) -> str:
        if len(output) <= self.chunk_model_limit_chars:
            return await self._compress_with_model(output, metadata)

        parts = [
            output[idx: idx + self.max_chunk_chars]
            for idx in range(0, len(output), self.max_chunk_chars)
        ]
        compressed_parts: list[str] = []
        for index, part in enumerate(parts):
            part_metadata = dict(metadata)
            part_metadata["chunk_index"] = index
            part_metadata["chunk_count"] = len(parts)
            compressed_parts.append(await self._compress_with_model(part, part_metadata))
        return "\n".join(compressed_parts)

    async def _compress_with_model(self, output: str, metadata: dict[str, Any]) -> str:
        prompt = (
            "You are an output compressor for developer tool logs.\n"
            f"Compress the content to <= {self.target_chars} characters while preserving:\n"
            "- Key outcomes, errors, stack traces, exit codes, file paths, and next actions.\n"
            "- If output is success only, keep concise summary and important artifacts.\n"
            "- If output mixes success and errors, separate both clearly.\n"
            "Return plain text only."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": output},
        ]
        lightweight_model = self.router.get_model_for_task("lightweight")
        session_id = metadata.get("session_id")
        try:
            response = await self.router.call(
                lightweight_model,
                messages,
                session_id=session_id,
            )
            if not isinstance(response, str):
                return str(response)
            return response
        except Exception as exc:  # pragma: no cover - fallback safeguard
            logger.warning(
                "output_compressor.model_failed",
                error=str(exc),
                session_id=session_id,
                tool_name=metadata.get("tool_name"),
            )
            return output[: self.target_chars]

    def _build_marker(self, original_chars: int, compressed_chars: int) -> tuple[str, int]:
        marker = (
            f"[📦 Output compressed from {original_chars} → {compressed_chars} chars. "
            "Original saved to logs. Ask me for details.]"
        )
        body_budget = self.target_chars - len(marker) - 1
        return marker, body_budget

    def _finalize_with_marker(self, *, original_chars: int, body: str) -> str:
        compressed_chars = len(body)
        final_output = ""
        for _ in range(3):
            marker, body_budget = self._build_marker(original_chars, compressed_chars)
            if body_budget <= 0:
                final_output = marker[: self.target_chars]
                compressed_chars = len(final_output)
                break
            safe_body = body[:body_budget]
            final_output = f"{safe_body}\n{marker}"
            compressed_chars = len(final_output)
            if len(final_output) <= self.target_chars:
                break
        if len(final_output) > self.target_chars:
            final_output = final_output[: self.target_chars]
        return final_output

    def _save_original(self, output: str, metadata: dict[str, Any]) -> str:
        cache_id = uuid4().hex
        self._recent_originals[cache_id] = {
            "output": output,
            "metadata": dict(metadata),
            "created_at": datetime.now(UTC).isoformat(),
        }
        while len(self._recent_originals) > self.cache_size:
            self._recent_originals.popitem(last=False)
        return cache_id

    def _attach_compressed_meta(
        self,
        *,
        metadata: dict[str, Any],
        cache_id: str,
        original_chars: int,
        compressed_chars: int,
    ) -> None:
        metadata["compressed_meta"] = {
            "cache_id": cache_id,
            "original_chars": original_chars,
            "compressed_chars": compressed_chars,
        }
