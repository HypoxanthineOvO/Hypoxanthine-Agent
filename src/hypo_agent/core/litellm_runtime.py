from __future__ import annotations

try:
    import litellm

    # LiteLLM defaults to an aiohttp-backed transport that can leave
    # transient ClientSession warnings on shutdown in our runtime.
    litellm.disable_aiohttp_transport = True

    from litellm import acompletion as litellm_acompletion
    from litellm import aembedding as litellm_aembedding
    from litellm.exceptions import OpenAIError as LiteLLMOpenAIError
except ImportError:  # pragma: no cover - depends on runtime environment
    litellm = None
    litellm_acompletion = None
    litellm_aembedding = None
    LiteLLMOpenAIError = None


def aiohttp_transport_disabled() -> bool:
    return bool(getattr(litellm, "disable_aiohttp_transport", False))
