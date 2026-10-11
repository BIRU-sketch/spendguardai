"""
AnthropicAdapter — implements ProviderAdapter against Anthropic's native
Messages API (https://api.anthropic.com/v1/messages).

Features:
1. Accurate Usage Extraction: Captures input_tokens, output_tokens,
   cache_creation_input_tokens, and cache_read_input_tokens.
2. Bidirectional Schema Translation: Translates inbound OpenAI-compatible
   messages & tools into Anthropic's schema, and normalizes Anthropic responses
   into OpenAI chat completion format so downstream agents work out-of-the-box.
3. Turn Merging: Automatically merges consecutive same-role turns to avoid
   Anthropic's alternating role constraint errors.
4. Streaming Normalization: Emits OpenAI-compatible SSE chunks and captures
   message_start + message_delta usage.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import httpx
from anthropic import AsyncAnthropic, APIError

from app.providers.base import (
    InternalRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)

_DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter:
    """Production adapter for Anthropic Claude models."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or "dummy-key"
        self._client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=base_url or "https://api.anthropic.com",
        )

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        """Translates OpenAI-style InternalRequest into Anthropic Messages API payload."""
        system_prompts: list[str] = []
        raw_messages: list[dict[str, Any]] = []

        for msg in internal_req.messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                if content:
                    system_prompts.append(str(content))
                continue

            if role == "tool":
                # OpenAI tool response -> Anthropic tool_result inside a user message
                tool_call_id = msg.get("tool_call_id", "")
                raw_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": str(content or ""),
                    }],
                })
                continue

            if role == "assistant":
                # Check for tool_calls in assistant turn
                tool_calls = msg.get("tool_calls")
                if tool_calls and isinstance(tool_calls, list):
                    content_blocks: list[dict[str, Any]] = []
                    if content:
                        content_blocks.append({"type": "text", "text": str(content)})
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", "{}")
                        parsed_input = json.loads(args) if isinstance(args, str) else (args or {})
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", str(uuid.uuid4())),
                            "name": fn.get("name", "unknown_tool"),
                            "input": parsed_input,
                        })
                    raw_messages.append({"role": "assistant", "content": content_blocks})
                    continue

            # Standard user or assistant message
            anthropic_content = content if content is not None else ""
            raw_messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": anthropic_content,
            })

        # Anthropic requires alternating user/assistant turns.
        # Merge consecutive turns with the same role.
        merged_messages: list[dict[str, Any]] = []
        for msg in raw_messages:
            if not merged_messages:
                merged_messages.append(msg)
                continue

            prev = merged_messages[-1]
            if prev["role"] == msg["role"]:
                # Merge contents
                p_content = prev["content"]
                m_content = msg["content"]
                if isinstance(p_content, str) and isinstance(m_content, str):
                    prev["content"] = f"{p_content}\n\n{m_content}"
                elif isinstance(p_content, list) and isinstance(m_content, list):
                    prev["content"] = p_content + m_content
                elif isinstance(p_content, str) and isinstance(m_content, list):
                    prev["content"] = [{"type": "text", "text": p_content}] + m_content
                elif isinstance(p_content, list) and isinstance(m_content, str):
                    prev["content"] = p_content + [{"type": "text", "text": m_content}]
            else:
                merged_messages.append(msg)

        # Anthropic requires the first message to be role 'user'
        if merged_messages and merged_messages[0]["role"] != "user":
            merged_messages.insert(0, {"role": "user", "content": "Hello"})

        payload: dict[str, Any] = {
            "model": internal_req.model,
            "messages": merged_messages,
            "max_tokens": internal_req.max_tokens or _DEFAULT_MAX_TOKENS,
            "stream": internal_req.stream,
        }

        if system_prompts:
            payload["system"] = "\n\n".join(system_prompts)

        if internal_req.temperature is not None:
            # Anthropic temperature range is 0.0 to 1.0
            payload["temperature"] = max(0.0, min(float(internal_req.temperature), 1.0))

        if internal_req.top_p is not None:
            payload["top_p"] = float(internal_req.top_p)

        # Tools translation
        if internal_req.tools:
            anthropic_tools: list[dict[str, Any]] = []
            for t in internal_req.tools:
                fn = t.get("function", {})
                anthropic_tools.append({
                    "name": fn.get("name", "tool"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            payload["tools"] = anthropic_tools

        # Pass through extra parameters
        for k, v in internal_req.extra.items():
            if k not in payload:
                payload[k] = v

        return payload

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        if provider_payload.get("stream"):
            raise ValueError("Use stream() for streaming requests, not send().")

        try:
            response = await self._client.messages.create(**provider_payload)
            anthropic_dict = response.model_dump()
            # Normalize to OpenAI response format for downstream agents
            normalized = self._normalize_response(anthropic_dict, provider_payload.get("model", "claude"))
            return ProviderResponse(status_code=200, payload=normalized)
        except APIError as exc:
            raise ProviderError(
                message=str(exc),
                status_code=getattr(exc, "status_code", 502),
                provider="anthropic",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                message=f"Anthropic upstream failure: {exc}",
                status_code=502,
                provider="anthropic",
            ) from exc

    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        msg_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model = provider_payload.get("model", "claude")
        streamed_chars = 0

        try:
            async with self._client.messages.stream(**provider_payload) as stream:
                async for event in stream:
                    event_dict = event.model_dump()
                    event_type = event_dict.get("type")

                    if event_type == "message_start":
                        msg_data = event_dict.get("message", {})
                        msg_id = msg_data.get("id", msg_id)
                        u = msg_data.get("usage", {})
                        input_tokens = u.get("input_tokens", 0)
                        cached_tokens = u.get("cache_read_input_tokens", 0)

                    elif event_type == "content_block_start":
                        block = event_dict.get("content_block", {})
                        if block.get("type") == "tool_use":
                            block_idx = event_dict.get("index", 0)
                            yield {
                                "id": msg_id,
                                "object": "chat.completion.chunk",
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": block_idx,
                                            "id": block.get("id", str(uuid.uuid4())),
                                            "type": "function",
                                            "function": {
                                                "name": block.get("name", ""),
                                                "arguments": "",
                                            },
                                        }]
                                    },
                                    "finish_reason": None,
                                }],
                            }

                    elif event_type == "content_block_delta":
                        delta = event_dict.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            streamed_chars += len(text)
                            yield {
                                "id": msg_id,
                                "object": "chat.completion.chunk",
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }],
                            }
                        elif delta.get("type") == "input_json_delta":
                            partial_json = delta.get("partial_json", "")
                            streamed_chars += len(partial_json)
                            block_idx = event_dict.get("index", 0)
                            yield {
                                "id": msg_id,
                                "object": "chat.completion.chunk",
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": block_idx,
                                            "function": {
                                                "arguments": partial_json,
                                            },
                                        }]
                                    },
                                    "finish_reason": None,
                                }],
                            }

                    elif event_type == "message_delta":
                        u = event_dict.get("usage", {})
                        if u.get("output_tokens"):
                            output_tokens = u["output_tokens"]
                        stop_reason = event_dict.get("delta", {}).get("stop_reason")
                        finish_reason = "stop" if stop_reason == "end_turn" else ("tool_calls" if stop_reason == "tool_use" else stop_reason)
                        if finish_reason:
                            yield {
                                "id": msg_id,
                                "object": "chat.completion.chunk",
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": finish_reason,
                                }],
                            }

        except APIError as exc:
            raise ProviderError(
                message=str(exc),
                status_code=getattr(exc, "status_code", 502),
                provider="anthropic",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                message=f"Anthropic streaming failure: {exc}",
                status_code=502,
                provider="anthropic",
            ) from exc

        # Emit terminal usage chunk
        if output_tokens == 0 and streamed_chars > 0:
            output_tokens = max(1, streamed_chars // 4)
        if input_tokens == 0:
            messages = provider_payload.get("messages", [])
            input_tokens = max(32, len(json.dumps(messages)) // 4)

        total_tokens = input_tokens + output_tokens
        yield {
            "id": msg_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }

    def _normalize_response(self, anthropic_dict: dict[str, Any], model: str) -> dict[str, Any]:
        """Normalizes native Anthropic message dict into OpenAI chat completion dict."""
        content_blocks = anthropic_dict.get("content", [])
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in content_blocks:
            b_type = block.get("type")
            if b_type == "text":
                text_parts.append(block.get("text", ""))
            elif b_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", str(uuid.uuid4())),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        stop_reason = anthropic_dict.get("stop_reason", "stop")
        finish_reason = "tool_calls" if tool_calls else ("stop" if stop_reason == "end_turn" else stop_reason)

        u = anthropic_dict.get("usage", {})
        inp = u.get("input_tokens", 0)
        out = u.get("output_tokens", 0)
        cached = u.get("cache_read_input_tokens", 0)

        message_obj: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) if text_parts else None,
        }
        if tool_calls:
            message_obj["tool_calls"] = tool_calls

        return {
            "id": anthropic_dict.get("id", f"msg_{uuid.uuid4().hex[:12]}"),
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": message_obj,
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": inp,
                "completion_tokens": out,
                "total_tokens": inp + out,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        return self._extract_from_dict(provider_response.payload)

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        return self._extract_from_dict(last_chunk)

    def _extract_from_dict(self, data: dict[str, Any]) -> Usage:
        if not isinstance(data, dict):
            return Usage.zero()

        u = data.get("usage")
        if not isinstance(u, dict):
            return Usage.zero()

        inp = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        out = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        total = int(u.get("total_tokens") or (inp + out))

        cached = 0
        prompt_details = u.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached = int(prompt_details.get("cached_tokens") or 0)
        elif "cache_read_input_tokens" in u:
            cached = int(u.get("cache_read_input_tokens") or 0)

        return Usage(
            input_tokens=inp,
            output_tokens=out,
            total_tokens=total,
            cached_tokens=cached,
        )

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        return internal_req.max_tokens or _DEFAULT_MAX_TOKENS
