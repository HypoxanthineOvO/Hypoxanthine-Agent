from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog
try:
    from litellm import acompletion as litellm_acompletion
except ImportError:  # pragma: no cover - depends on runtime environment
    litellm_acompletion = None

from hypo_agent.core.config_loader import RuntimeModelConfig


class ModelRouter:
    def __init__(
        self,
        config: RuntimeModelConfig,
        acompletion_fn=None,
    ) -> None:
        self.config = config
        self._acompletion = acompletion_fn or litellm_acompletion
        if self._acompletion is None:
            raise RuntimeError(
                "litellm is not installed and no acompletion_fn was provided"
            )
        self.logger = structlog.get_logger("hypo_agent.model_router")

    async def call(self, model_name: str, messages: list[dict[str, Any]]) -> str:
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in self._candidate_chain(model_name):
            cfg = self.config.models[candidate]
            if cfg.provider is None or cfg.litellm_model is None:
                attempted.append(f"{candidate}(skipped)")
                self.logger.info(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="provider_or_litellm_model_missing",
                )
                continue

            try:
                response = await self._acompletion(
                    model=cfg.litellm_model,
                    messages=messages,
                    api_base=cfg.api_base,
                    api_key=cfg.api_key,
                )
                text = self._extract_message_text(response)
                usage = self._extract_usage(response)
                self.logger.info(
                    "model_call_success",
                    requested_model=model_name,
                    resolved_model=candidate,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["total_tokens"],
                )
                return text
            except Exception as exc:  # pragma: no cover - exercised in tests
                attempted.append(candidate)
                last_error = exc
                self.logger.warning(
                    "model_call_failed",
                    requested_model=model_name,
                    resolved_model=candidate,
                    error=str(exc),
                )

        raise RuntimeError(
            f"All models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in self._candidate_chain(model_name):
            cfg = self.config.models[candidate]
            if cfg.provider is None or cfg.litellm_model is None:
                attempted.append(f"{candidate}(skipped)")
                self.logger.info(
                    "model_skipped",
                    requested_model=model_name,
                    resolved_model=candidate,
                    reason="provider_or_litellm_model_missing",
                )
                continue

            started = False
            usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

            try:
                response_stream = await self._acompletion(
                    model=cfg.litellm_model,
                    messages=messages,
                    api_base=cfg.api_base,
                    api_key=cfg.api_key,
                    stream=True,
                )

                async for chunk in response_stream:
                    usage = self._extract_usage(chunk, default=usage)
                    text = self._extract_delta_text(chunk)
                    if not text:
                        continue
                    started = True
                    yield text

                self.logger.info(
                    "model_stream_success",
                    requested_model=model_name,
                    resolved_model=candidate,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["total_tokens"],
                )
                return
            except Exception as exc:  # pragma: no cover - exercised in tests
                if started:
                    self.logger.error(
                        "model_stream_failed_after_output",
                        requested_model=model_name,
                        resolved_model=candidate,
                        error=str(exc),
                    )
                    raise

                attempted.append(candidate)
                last_error = exc
                self.logger.warning(
                    "model_stream_failed",
                    requested_model=model_name,
                    resolved_model=candidate,
                    error=str(exc),
                )

        raise RuntimeError(
            f"All stream models failed for '{model_name}'. Attempted chain: {attempted}"
        ) from last_error

    def _candidate_chain(self, start_model: str) -> list[str]:
        if start_model not in self.config.models:
            raise ValueError(f"Unknown model '{start_model}'")

        chain: list[str] = []
        visited: set[str] = set()
        current = start_model
        while current is not None:
            if current in visited:
                raise ValueError(f"Fallback cycle detected at '{current}'")
            if current not in self.config.models:
                raise ValueError(f"Model '{current}' not found in configuration")
            visited.add(current)
            chain.append(current)
            current = self.config.models[current].fallback
        return chain

    def _extract_message_text(self, payload: Any) -> str:
        choices = self._read_field(payload, "choices") or []
        if not choices:
            return ""

        first_choice = choices[0]
        message = self._read_field(first_choice, "message")
        if message is None:
            return ""

        content = self._read_field(message, "content")
        return self._normalize_content(content)

    def _extract_delta_text(self, chunk: Any) -> str:
        choices = self._read_field(chunk, "choices") or []
        if not choices:
            return ""
        first_choice = choices[0]
        delta = self._read_field(first_choice, "delta")
        if delta is None:
            return ""
        return self._normalize_content(self._read_field(delta, "content"))

    def _normalize_content(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                text = self._read_field(item, "text")
                if isinstance(text, str):
                    text_parts.append(text)
            return "".join(text_parts)
        return str(content)

    def _extract_usage(
        self,
        payload: Any,
        default: dict[str, int | None] | None = None,
    ) -> dict[str, int | None]:
        usage = self._read_field(payload, "usage")
        if usage is None:
            return default or {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            }

        prompt = self._read_field(usage, "prompt_tokens")
        completion = self._read_field(usage, "completion_tokens")
        total = self._read_field(usage, "total_tokens")
        return {
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": total,
        }

    def _read_field(self, payload: Any, key: str) -> Any:
        if isinstance(payload, dict):
            return payload.get(key)
        return getattr(payload, key, None)
