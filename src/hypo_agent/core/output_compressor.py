from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger("hypo_agent.output_compressor")

COMPRESSION_STRATEGIES = {
    "web_search": "保留每条搜索结果的标题、URL和关键摘要句。不要合并条目。",
    "web_read": "保留页面的主要内容结构、关键数据点和具体数值。去除导航栏、广告、页脚等无关内容。",
    "run_command": "保留命令的退出状态、错误信息、关键输出行。如果是日志，保留 ERROR/WARNING 级别的完整行。不要丢失文件名、行号、错误消息。",
    "run_code": "保留代码执行结果、错误堆栈的完整 traceback、assert 失败信息。不要丢失具体数值。",
    "get_error_summary": "这是诊断信息，保留所有错误条目的完整内容，包括时间戳、错误类型、错误消息。不要摘要化。",
    "get_tool_history": "保留每条工具调用记录的状态、时间、错误信息。不要合并或省略条目。",
    "get_recent_logs": "保留所有 ERROR/WARNING 级别日志的完整内容。INFO 级别可以摘要。",
    "_default": "保留关键数据点、具体数值、文件名、URL。保持原始信息的结构层次。不要把列表合并成一句话概述。",
}


class OutputCompressor:
    def __init__(
        self,
        *,
        router: Any,
        threshold_chars: int = 20000,
        target_chars: int = 8000,
        max_chunk_chars: int = 80 * 1024,
        chunk_model_limit_chars: int = 128 * 1024,
        max_iterations: int = 3,
        cache_size: int = 10,
        hard_threshold_chars: int = 64000,
        threshold_tokens: int = 5000,
        hard_threshold_tokens: int = 16000,
        line_threshold: int = 220,
        structured_passthrough_chars: int = 20000,
        structured_line_threshold: int = 260,
        truncation_chars: int = 60000,
    ) -> None:
        self.router = router
        self.threshold_chars = threshold_chars
        self.target_chars = target_chars
        self.max_chunk_chars = max_chunk_chars
        self.chunk_model_limit_chars = chunk_model_limit_chars
        self.max_iterations = max_iterations
        self.cache_size = cache_size
        self.hard_threshold_chars = max(self.threshold_chars, hard_threshold_chars)
        self.threshold_tokens = max(1, int(threshold_tokens))
        self.hard_threshold_tokens = max(self.threshold_tokens, int(hard_threshold_tokens))
        self.line_threshold = max(1, line_threshold)
        self.structured_passthrough_chars = max(
            self.threshold_chars,
            structured_passthrough_chars,
        )
        self.structured_line_threshold = max(1, structured_line_threshold)
        self.truncation_chars = max(1, min(int(truncation_chars), self.hard_threshold_chars))
        self._recent_originals: OrderedDict[str, dict[str, Any]] = OrderedDict()

    async def compress_if_needed(
        self,
        output: str,
        metadata: dict[str, Any],
        *,
        tool_name: str | None = None,
    ) -> tuple[str, bool]:
        resolved_tool_name = str(tool_name or metadata.get("tool_name") or "").strip() or None
        if len(output) > self.hard_threshold_chars:
            cache_id = self._save_original(output, metadata)
            final_output = self._truncate_output(output)
            self._attach_compressed_meta(
                metadata=metadata,
                cache_id=cache_id,
                original_chars=len(output),
                compressed_chars=len(final_output),
            )
            return final_output, True

        if not self._should_compress(output):
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
            next_output = await self._compress_round(
                working,
                metadata,
                tool_name=resolved_tool_name,
            )
            if not next_output:
                break
            working = next_output

        marker, body_budget = self._build_reference_line(len(output), len(working))
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

    def _should_compress(self, output: str) -> bool:
        if len(output) <= self.threshold_chars:
            return False
        return True

    def _analyze_output(self, output: str) -> dict[str, Any]:
        extracted_text = output
        is_structured = False
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            parsed = None
        else:
            is_structured = not isinstance(parsed, str)
            preferred = self._extract_text_for_analysis(parsed)
            if preferred:
                extracted_text = preferred

        line_count = extracted_text.count("\n") + 1 if extracted_text else 0
        return {
            "chars": len(output),
            "line_count": line_count,
            "is_structured": is_structured,
        }

    def _extract_text_for_analysis(self, payload: Any) -> str:
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                chunks = [
                    value
                    for key in ("stdout", "stderr", "text", "output", "content")
                    if isinstance((value := result.get(key)), str) and value
                ]
                if chunks:
                    return "\n".join(chunks)

            chunks = [
                value
                for key in ("text", "summary", "content", "message", "error_info")
                if isinstance((value := payload.get(key)), str) and value
            ]
            if chunks:
                return "\n".join(chunks)

        if isinstance(payload, list):
            chunks = [item for item in payload if isinstance(item, str) and item]
            if chunks:
                return "\n".join(chunks)

        return ""

    def get_recent_originals(self) -> dict[str, dict[str, Any]]:
        return dict(self._recent_originals)

    def get_original_output(self, cache_id: str) -> str | None:
        item = self._recent_originals.get(cache_id)
        if item is None:
            return None
        output = item.get("output")
        return str(output) if isinstance(output, str) else None

    async def _compress_round(
        self,
        output: str,
        metadata: dict[str, Any],
        *,
        tool_name: str | None,
    ) -> str:
        if len(output) <= self.chunk_model_limit_chars:
            return await self._compress_with_model(output, metadata, tool_name=tool_name)

        parts = [
            output[idx: idx + self.max_chunk_chars]
            for idx in range(0, len(output), self.max_chunk_chars)
        ]
        compressed_parts: list[str] = []
        for index, part in enumerate(parts):
            part_metadata = dict(metadata)
            part_metadata["chunk_index"] = index
            part_metadata["chunk_count"] = len(parts)
            compressed_parts.append(
                await self._compress_with_model(part, part_metadata, tool_name=tool_name)
            )
        return "\n".join(compressed_parts)

    async def _compress_with_model(
        self,
        output: str,
        metadata: dict[str, Any],
        *,
        tool_name: str | None,
    ) -> str:
        strategy = COMPRESSION_STRATEGIES.get(tool_name or "", COMPRESSION_STRATEGIES["_default"])
        prompt = (
            "You are an output compressor for developer tool logs.\n"
            f"Tool name: {tool_name or 'unknown'}\n"
            f"Compression strategy: {strategy}\n"
            f"Compress the content to <= {self.target_chars} characters while preserving:\n"
            "- Key outcomes, errors, stack traces, exit codes, file paths, and next actions.\n"
            "- If output is success only, keep concise summary and important artifacts.\n"
            "- If output mixes success and errors, separate both clearly.\n"
            "- Preserve structured lists when they matter; do not collapse distinct records into one sentence.\n"
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
        except (OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover - fallback safeguard
            logger.warning(
                "output_compressor.model_failed",
                error=str(exc),
                session_id=session_id,
                tool_name=metadata.get("tool_name"),
            )
            return output[: self.target_chars]

    def _build_reference_line(self, original_chars: int, compressed_chars: int) -> tuple[str, int]:
        marker = (
            f'[\U0001F4E6 原始输出 {original_chars} 字符，已压缩至 {compressed_chars} 字符。'
            '如需查看原文请说"给我看原始输出"]'
        )
        body_budget = self.target_chars - len(marker) - 1
        return marker, body_budget

    def _finalize_with_marker(self, *, original_chars: int, body: str) -> str:
        compressed_chars = len(body)
        final_output = ""
        for _ in range(3):
            marker, body_budget = self._build_reference_line(original_chars, compressed_chars)
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

    def _truncate_output(self, output: str) -> str:
        notice = "[\u8f93\u51fa\u8fc7\u957f\u5df2\u622a\u65ad\uff0c\u4ec5\u4fdd\u7559\u524d 60000 \u5b57\u7b26]"
        body = output[: self.truncation_chars]
        compressed_chars = len(body)
        final_output = body
        for _ in range(3):
            reference = (
                f'[\U0001F4E6 原始输出 {len(output)} 字符，已压缩至 '
                f'{compressed_chars} 字符。如需查看原文请说"给我看原始输出"]'
            )
            available_body = max(
                0,
                self.hard_threshold_chars - len(notice) - len(reference) - 2,
            )
            safe_body = body[:available_body]
            final_output = f"{safe_body}\n{notice}\n{reference}"
            compressed_chars = len(final_output)
            if len(final_output) <= self.hard_threshold_chars:
                break
        return final_output[: self.hard_threshold_chars]

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
