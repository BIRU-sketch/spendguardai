"""
Provider Registry & Resolver — routes requests to the correct adapter
based on model names, explicit headers, and available provider credentials.
"""

from __future__ import annotations

import os
from typing import Any

from app.config import config
from app.providers.base import ProviderAdapter
from app.providers.openai_compatible import OpenAICompatibleAdapter
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.gemini_adapter import GeminiAdapter


class ProviderRegistry:
    """Singleton registry that instantiates, caches, and routes to provider adapters."""

    def __init__(self) -> None:
        self._cache: dict[str, ProviderAdapter] = {}

    def get_adapter(
        self,
        *,
        model: str,
        provider_hint: str | None = None,
    ) -> ProviderAdapter:
        """Resolves the best ProviderAdapter for a given model or hint."""
        provider_name = (provider_hint or self._infer_provider(model)).lower().strip()

        if provider_name == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY") or config.upstream_base_key
            base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            cache_key = f"anthropic:{base_url}:{api_key[:8] if api_key else ''}"
            if cache_key not in self._cache:
                self._cache[cache_key] = AnthropicAdapter(api_key=api_key, base_url=base_url)
            return self._cache[cache_key]

        elif provider_name == "gemini":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or config.upstream_base_key
            base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
            cache_key = f"gemini:{base_url}:{api_key[:8] if api_key else ''}"
            if cache_key not in self._cache:
                self._cache[cache_key] = GeminiAdapter(api_key=api_key, base_url=base_url)
            return self._cache[cache_key]

        elif provider_name == "groq":
            api_key = os.getenv("GROQ_API_KEY") or config.upstream_base_key
            base_url = "https://api.groq.com/openai/v1"
            cache_key = f"groq:{base_url}:{api_key[:8] if api_key else ''}"
            if cache_key not in self._cache:
                self._cache[cache_key] = OpenAICompatibleAdapter(base_url=base_url, api_key=api_key)
            return self._cache[cache_key]

        elif provider_name == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY") or config.upstream_base_key
            base_url = "https://openrouter.ai/api/v1"
            cache_key = f"openrouter:{base_url}:{api_key[:8] if api_key else ''}"
            if cache_key not in self._cache:
                self._cache[cache_key] = OpenAICompatibleAdapter(base_url=base_url, api_key=api_key)
            return self._cache[cache_key]

        # Default: OpenAI or configured upstream
        base_url = config.upstream_base_url or "https://api.openai.com/v1"
        api_key = os.getenv("OPENAI_API_KEY") or config.upstream_base_key
        cache_key = f"openai_compat:{base_url}:{api_key[:8] if api_key else ''}"
        if cache_key not in self._cache:
            self._cache[cache_key] = OpenAICompatibleAdapter(base_url=base_url, api_key=api_key)
        return self._cache[cache_key]

    def _infer_provider(self, model: str) -> str:
        """Infers the provider from model name prefixes."""
        m = model.lower()

        if m.startswith("openrouter/"):
            return "openrouter"

        if m.startswith("claude") or "anthropic/" in m:
            return "anthropic"

        if m.startswith("gemini") or "google/" in m:
            return "gemini"

        if m.startswith("groq/"):
            return "groq"

        if "llama-" in m and os.getenv("GROQ_API_KEY"):
            return "groq"

        return "openai"


registry = ProviderRegistry()
