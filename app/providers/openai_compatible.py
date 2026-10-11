"""
OpenAICompatibleAdapter — implements ProviderAdapter against any endpoint
that speaks the OpenAI chat completions API: OpenAI itself, Grok, Groq,
OpenRouter, DeepSeek, Together, Ollama, and Gemini's compat layer.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError

from app.providers.base import (
    InternalRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)


class OpenAICompatibleAdapter:
    """Adapter for OpenAI and OpenAI-compatible providers."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key or "sk-dummy",
        )

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": internal_req.model,
            "messages": internal_req.messages,
            "stream": internal_req.stream,
            **internal_req.extra,
        }

        # Handle o1 / o3 reasoning model parameter differences
        is_o_series = any(internal_req.model.startswith(p) for p in ("o1", "o3", "o1-", "o3-"))

        if internal_req.max_tokens is not None:
            if is_o_series:
                payload["max_completion_tokens"] = internal_req.max_tokens
            else:
                payload["max_tokens"] = internal_req.max_tokens

        if internal_req.temperature is not None and not is_o_series:
            payload["temperature"] = internal_req.temperature

        if internal_req.top_p is not None and not is_o_series:
            payload["top_p"] = internal_req.top_p

        if internal_req.tools is not None:
            payload["tools"] = internal_req.tools
        if internal_req.tool_choice is not None:
            payload["tool_choice"] = internal_req.tool_choice

        # Ensure streaming responses report usage on the final chunk
        if internal_req.stream:
            payload["stream_options"] = {"include_usage": True}

        return payload

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        if provider_payload.get("stream"):
            raise ValueError("Use stream() for streaming requests, not send().")

        try:
            response = await self._client.chat.completions.create(**provider_payload)
            payload_dict = response.model_dump()
            return ProviderResponse(status_code=200, payload=payload_dict)
        except APIError as exc:
            raise ProviderError(
                message=str(exc),
                status_code=getattr(exc, "status_code", 502),
                provider="openai_compatible",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                message=f"Upstream communication failure: {exc}",
                status_code=502,
                provider="openai_compatible",
            ) from exc

    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        accumulated_usage: dict[str, Any] | None = None
        last_chunk_had_usage = False
        streamed_chars = 0

        try:
            stream = await self._client.chat.completions.create(**provider_payload)
            async for chunk in stream:
                chunk_dict = chunk.model_dump()
                if chunk_dict.get("usage"):
                    accumulated_usage = chunk_dict["usage"]
                    last_chunk_had_usage = True
                else:
                    last_chunk_had_usage = False

                choices = chunk_dict.get("choices")
                if choices and isinstance(choices, list) and len(choices) > 0:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        streamed_chars += len(content)

                yield chunk_dict
        except APIError as exc:
            raise ProviderError(
                message=str(exc),
                status_code=getattr(exc, "status_code", 502),
                provider="openai_compatible",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                message=f"Upstream streaming failure: {exc}",
                status_code=502,
                provider="openai_compatible",
            ) from exc

        # If usage arrived earlier in the stream but not in the very last chunk,
        # emit a synthetic final chunk so extract_stream_usage(last_chunk) is guaranteed to find it.
        if accumulated_usage and not last_chunk_had_usage:
            yield {
                "id": "usage-terminal-summary",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": accumulated_usage,
            }
        elif not accumulated_usage and streamed_chars > 0:
            # Fallback if provider didn't emit usage on stream
            messages = provider_payload.get("messages", [])
            fallback_in = max(32, len(json.dumps(messages)) // 4)
            fallback_out = max(1, streamed_chars // 4)
            yield {
                "id": "usage-terminal-summary",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": fallback_in,
                    "completion_tokens": fallback_out,
                    "total_tokens": fallback_in + fallback_out,
                },
            }

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        raw_usage = provider_response.payload.get("usage")
        return self._parse_usage_dict(raw_usage or provider_response.payload)

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        raw_usage = last_chunk.get("usage")
        return self._parse_usage_dict(raw_usage or last_chunk)

    def _parse_usage_dict(self, data: dict[str, Any]) -> Usage:
        if not isinstance(data, dict):
            return Usage.zero()

        usage = data.get("usage", data)
        if not isinstance(usage, dict):
            return Usage.zero()

        # Standard OpenAI fields
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        # Ollama / vLLM fallbacks
        if prompt_tokens is None and "prompt_eval_count" in usage:
            prompt_tokens = usage.get("prompt_eval_count", 0)
        if completion_tokens is None and "eval_count" in usage:
            completion_tokens = usage.get("eval_count", 0)

        p_tokens = int(prompt_tokens or 0)
        c_tokens = int(completion_tokens or 0)
        t_tokens = int(total_tokens or (p_tokens + c_tokens))

        # Cached tokens (OpenAI prompt_tokens_details.cached_tokens)
        cached_tokens = 0
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = int(prompt_details.get("cached_tokens") or 0)

        # Reasoning tokens (OpenAI completion_tokens_details.reasoning_tokens)
        reasoning_tokens = 0
        comp_details = usage.get("completion_tokens_details")
        if isinstance(comp_details, dict):
            reasoning_tokens = int(comp_details.get("reasoning_tokens") or 0)

        return Usage(
            input_tokens=p_tokens,
            output_tokens=c_tokens,
            total_tokens=t_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        if internal_req.max_tokens is not None:
            return internal_req.max_tokens
        return 4096
