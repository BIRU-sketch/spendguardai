"""
OpenAICompatibleAdapter — implements ProviderAdapter against any endpoint
that speaks the OpenAI chat completions API: OpenAI itself, Grok, Groq,
OpenRouter, Gemini's compatibility layer, etc. Only base_url + api_key
change between them.
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI, APIError

from app.providers.base import (
    InternalRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)


class OpenAICompatibleAdapter:
    """One instance per (base_url, api_key) pair — a gateway serving
    multiple providers holds one adapter instance per provider, not one
    shared global instance."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": internal_req.model,
            "messages": internal_req.messages,
            "stream": internal_req.stream,
            **internal_req.extra,
        }
        if internal_req.max_tokens is not None:
            payload["max_tokens"] = internal_req.max_tokens
        if internal_req.temperature is not None:
            payload["temperature"] = internal_req.temperature

        # Without this, a streaming response's final chunk carries no
        # usage data at all — the API just won't report it. Not optional.
        if internal_req.stream:
            payload["stream_options"] = {"include_usage": True}

        return payload

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        if provider_payload.get("stream"):
            raise ValueError(
                "Use stream() for streaming requests, not send()."
            )

        try:
            response = await self._client.chat.completions.create(**provider_payload)
        except APIError as exc:
            raise ProviderError(str(exc)) from exc

        return ProviderResponse(status_code=200, payload=response.model_dump())

    async def stream(self, provider_payload: dict[str, Any]):
        try:
            stream = await self._client.chat.completions.create(**provider_payload)
            async for chunk in stream:
                yield chunk.model_dump()
        except APIError as exc:
            raise ProviderError(str(exc)) from exc

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        usage = last_chunk.get("usage")
        if not usage:
            return Usage.zero()

        return Usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        usage = provider_response.payload.get("usage")
        if not usage:
            return Usage.zero()

        return Usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        # An explicit cap is the real upper bound — use it directly.
        if internal_req.max_tokens is not None:
            return internal_req.max_tokens

        # No cap given: placeholder flat estimate, not a real heuristic
        # yet. Revisit once streaming.py exists and you can see how
        # badly this under- or over-reserves against real traffic.
        return 4096