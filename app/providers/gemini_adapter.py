"""
GeminiAdapter — implements ProviderAdapter against Google Gemini's native API
(https://generativelanguage.googleapis.com/v1beta/models/...).

Supports:
- Gemini 2.0 Flash, Gemini 1.5 Pro, Gemini 1.5 Flash, etc.
- Non-streaming generateContent & streaming streamGenerateContent?alt=sse.
- Native token usage extraction from usageMetadata (including cachedContentTokenCount).
- Schema normalization to/from OpenAI chat completions.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import httpx

from app.providers.base import (
    InternalRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)

_DEFAULT_MAX_TOKENS = 4096


class GeminiAdapter:
    """Adapter for Google Gemini models via direct REST API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        """Translates OpenAI chat completion request into Gemini payload."""
        system_texts: list[str] = []
        contents: list[dict[str, Any]] = []

        for msg in internal_req.messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                if content:
                    system_texts.append(str(content))
                continue

            gemini_role = "model" if role == "assistant" else "user"
            parts: list[dict[str, Any]] = []

            # Check for tool_calls in assistant turn
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
                    parts.append({
                        "functionCall": {
                            "name": fn.get("name", ""),
                            "args": parsed_args,
                        }
                    })

            # Check for tool response
            elif role == "tool":
                parts.append({
                    "functionResponse": {
                        "name": msg.get("name", "tool"),
                        "response": {"result": content},
                    }
                })

            if content:
                parts.append({"text": str(content)})

            if not parts:
                parts.append({"text": ""})

            contents.append({"role": gemini_role, "parts": parts})

        # Ensure alternating turns for Gemini
        merged_contents: list[dict[str, Any]] = []
        for c in contents:
            if not merged_contents:
                merged_contents.append(c)
                continue
            prev = merged_contents[-1]
            if prev["role"] == c["role"]:
                prev["parts"].extend(c["parts"])
            else:
                merged_contents.append(c)

        payload: dict[str, Any] = {
            "model": internal_req.model,
            "contents": merged_contents,
        }

        if system_texts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_texts)}]
            }

        generation_config: dict[str, Any] = {}
        if internal_req.temperature is not None:
            generation_config["temperature"] = float(internal_req.temperature)
        if internal_req.top_p is not None:
            generation_config["topP"] = float(internal_req.top_p)
        if internal_req.max_tokens is not None:
            generation_config["maxOutputTokens"] = int(internal_req.max_tokens)
        if generation_config:
            payload["generationConfig"] = generation_config

        # Tools translation
        if internal_req.tools:
            decls: list[dict[str, Any]] = []
            for t in internal_req.tools:
                fn = t.get("function", {})
                decls.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
            payload["tools"] = [{"functionDeclarations": decls}]

        payload["stream"] = internal_req.stream
        return payload

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        model = provider_payload.get("model", "gemini-1.5-flash")
        # Strip model prefix if passed as e.g. "models/gemini-..."
        clean_model = model.split("/")[-1]
        url = f"{self.base_url}/models/{clean_model}:generateContent?key={self.api_key}"

        body = {k: v for k, v in provider_payload.items() if k not in ("model", "stream")}

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, json=body)
            except Exception as exc:
                raise ProviderError(f"Gemini connection failed: {exc}", provider="gemini") from exc

        if resp.status_code != 200:
            raise ProviderError(
                f"Gemini API error ({resp.status_code}): {resp.text}",
                status_code=resp.status_code,
                provider="gemini",
            )

        gemini_dict = resp.json()
        normalized = self._normalize_response(gemini_dict, model)
        return ProviderResponse(status_code=200, payload=normalized)

    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        model = provider_payload.get("model", "gemini-1.5-flash")
        clean_model = model.split("/")[-1]
        url = f"{self.base_url}/models/{clean_model}:streamGenerateContent?alt=sse&key={self.api_key}"
        body = {k: v for k, v in provider_payload.items() if k not in ("model", "stream")}

        msg_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        accumulated_usage: dict[str, Any] | None = None
        streamed_chars = 0

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                async with client.stream("POST", url, json=body) as stream:
                    if stream.status_code != 200:
                        err_body = await stream.aread()
                        raise ProviderError(
                            f"Gemini stream error ({stream.status_code}): {err_body.decode('utf-8')}",
                            status_code=stream.status_code,
                            provider="gemini",
                        )

                    async for line in stream.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue

                        raw_json = line[len("data:"):].strip()
                        if raw_json == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(raw_json)
                        except json.JSONDecodeError:
                            continue

                        # Extract usageMetadata
                        if "usageMetadata" in chunk_data:
                            accumulated_usage = chunk_data["usageMetadata"]

                        candidates = chunk_data.get("candidates", [])
                        if candidates:
                            cand = candidates[0]
                            parts = cand.get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts if "text" in p)
                            finish_reason = cand.get("finishReason")
                            mapped_finish = "stop" if finish_reason == "STOP" else finish_reason

                            # Handle tool calls in stream
                            tool_calls = []
                            for p in parts:
                                if "functionCall" in p:
                                    fc = p["functionCall"]
                                    tool_calls.append({
                                        "index": 0,
                                        "id": f"call_{uuid.uuid4().hex[:8]}",
                                        "type": "function",
                                        "function": {
                                            "name": fc.get("name", ""),
                                            "arguments": json.dumps(fc.get("args", {})),
                                        },
                                    })
                                    mapped_finish = "tool_calls"

                            delta: dict[str, Any] = {}
                            if text:
                                delta["content"] = text
                                streamed_chars += len(text)
                            if tool_calls:
                                delta["tool_calls"] = tool_calls

                            if delta or mapped_finish:
                                yield {
                                    "id": msg_id,
                                    "object": "chat.completion.chunk",
                                    "model": model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": delta,
                                        "finish_reason": mapped_finish,
                                    }],
                                }

            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"Gemini streaming failed: {exc}", status_code=502, provider="gemini") from exc

        # Emit terminal usage chunk
        prompt_toks = accumulated_usage.get("promptTokenCount", 0) if accumulated_usage else max(32, len(json.dumps(body.get("contents", []))) // 4)
        comp_toks = accumulated_usage.get("candidatesTokenCount", 0) if accumulated_usage else max(1, streamed_chars // 4)
        cached_toks = accumulated_usage.get("cachedContentTokenCount", 0) if accumulated_usage else 0

        yield {
            "id": msg_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_toks,
                "completion_tokens": comp_toks,
                "total_tokens": prompt_toks + comp_toks,
                "prompt_tokens_details": {"cached_tokens": cached_toks},
            },
        }

    def _normalize_response(self, gemini_dict: dict[str, Any], model: str) -> dict[str, Any]:
        """Normalizes Gemini response dictionary into OpenAI chat completion dictionary."""
        candidates = gemini_dict.get("candidates", [])
        text_content = ""
        tool_calls: list[dict[str, Any]] = []
        finish_reason = "stop"

        if candidates:
            cand = candidates[0]
            parts = cand.get("content", {}).get("parts", [])
            for p in parts:
                if "text" in p:
                    text_content += p["text"]
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {})),
                        },
                    })
            fr = cand.get("finishReason")
            finish_reason = "tool_calls" if tool_calls else ("stop" if fr == "STOP" else str(fr or "stop"))

        u = gemini_dict.get("usageMetadata", {})
        inp = u.get("promptTokenCount", 0)
        out = u.get("candidatesTokenCount", 0)
        cached = u.get("cachedContentTokenCount", 0)

        message: dict[str, Any] = {
            "role": "assistant",
            "content": text_content if text_content else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
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
        if isinstance(u, dict):
            inp = int(u.get("prompt_tokens", 0))
            out = int(u.get("completion_tokens", 0))
            total = int(u.get("total_tokens", inp + out))
            cached = 0
            details = u.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached = int(details.get("cached_tokens", 0))
            return Usage(
                input_tokens=inp,
                output_tokens=out,
                total_tokens=total,
                cached_tokens=cached,
            )

        um = data.get("usageMetadata", {})
        if isinstance(um, dict) and um:
            inp = int(um.get("promptTokenCount", 0))
            out = int(um.get("candidatesTokenCount", 0))
            total = int(um.get("totalTokenCount", inp + out))
            cached = int(um.get("cachedContentTokenCount", 0))
            return Usage(
                input_tokens=inp,
                output_tokens=out,
                total_tokens=total,
                cached_tokens=cached,
            )

        return Usage.zero()

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        return internal_req.max_tokens or _DEFAULT_MAX_TOKENS
