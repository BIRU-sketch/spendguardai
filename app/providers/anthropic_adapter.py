"""
AnthropicAdapter — implements ProviderAdapter against Anthropic's native
Messages API. Not built on Anthropic's OpenAI-compat layer: Anthropic
explicitly documents that layer as intended for evaluation, not
production, with real limitations (temperature capped at 1.0, several
fields ignored, no prompt caching). A metering/enforcement product needs
the real schema, not a compatibility shim.

Two real schema differences from OpenAI this adapter has to bridge:
1. System prompts are a separate top-level field, not a role in the
   messages array.
2. Usage is split across two different streaming events (input_tokens
   on message_start, output_tokens on message_delta) — OpenAI reports
   both together on one final chunk. stream() accumulates both and
   yields one synthetic final usage chunk, so extract_stream_usage()
   keeps the same "read the last chunk" contract regardless of provider.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic, APIError

from app.providers.base import (
    InternalRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)

_DEFAULT_MAX_TOKENS = 4096  # Anthropic requires max_tokens; OpenAI doesn't.


class AnthropicAdapter:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        # Pull any system-role messages out into Anthropic's separate
        # `system` field — everything else stays as user/assistant turns.
        system_parts = [
            m["content"] for m in internal_req.messages if m.get("role") == "system"
        ]
        messages = [m for m in internal_req.messages if m.get("role") != "system"]

        payload: dict[str, Any] = {
            "model": internal_req.model,
            "messages": messages,
            "max_tokens": internal_req.max_tokens or _DEFAULT_MAX_TOKENS,
            "stream": internal_req.stream,
            **internal_req.extra,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if internal_req.temperature is not None:
            # Anthropic caps at 1.0, unlike OpenAI's 0-2 range — clamp
            # rather than let the API reject an otherwise-valid request.
            payload["temperature"] = min(internal_req.temperature, 1.0)

        return payload

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        if provider_payload.get("stream"):
            raise ValueError("Use stream() for streaming requests, not send().")

        try:
            response = await self._client.messages.create(**provider_payload)
        except APIError as exc:
            raise ProviderError(str(exc)) from exc

        return ProviderResponse(status_code=200, payload=response.model_dump())

    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0

        try:
            async with self._client.messages.stream(**provider_payload) as stream:
                async for event in stream:
                    chunk = event.model_dump()
                    yield chunk

                    if chunk.get("type") == "message_start":
                        input_tokens = chunk["message"]["usage"].get("input_tokens", 0)
                    elif chunk.get("type") == "message_delta":
                        output_tokens = chunk.get("usage", {}).get("output_tokens", 0)
        except APIError as exc:
            raise ProviderError(str(exc)) from exc

        # Synthetic final chunk — not a real Anthropic event, but it lets
        # extract_stream_usage() read combined usage the same way it does
        # for every other provider, without main.py needing to know
        # Anthropic split it across two events.
        yield {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        usage = provider_response.payload.get("usage")
        if not usage:
            return Usage.zero()

        return Usage.from_input_output(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        usage = last_chunk.get("usage")
        if not usage:
            return Usage.zero()

        return Usage.from_input_output(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        return internal_req.max_tokens or _DEFAULT_MAX_TOKENS