"""
ProviderAdapter interface — the boundary between SpendGuard's core logic
(quota checks, ledger, anomaly detection) and any specific LLM provider.

Nothing outside this module — and nothing outside app/providers/ — should
ever see a provider's raw request or response shape. Everything upstream
talks to InternalRequest and Usage only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable
@dataclass(frozen=True)
class InternalRequest:
    """Provider-agnostic representation of an inbound completion request."""

    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    # Anything provider-specific that doesn't need normalization
    # (top_p, tools, etc.) passes through here untouched.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResponse:
    """Raw response from the upstream provider. Opaque to everything
    except the adapter that produced it — only that adapter's
    extract_usage() should ever read .payload directly."""

    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    """Normalized token usage. Every adapter must produce this shape
    regardless of how its provider names or splits the fields —
    e.g. OpenAI's single usage.total_tokens vs. Anthropic's separate
    input_tokens/output_tokens with no combined total."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    @classmethod
    def from_input_output(cls, input_tokens: int, output_tokens: int) -> "Usage":
        """Convenience for adapters (like Anthropic's) that only report
        input/output separately — derives total instead of duplicating
        the addition in every adapter."""
        return cls(input_tokens, output_tokens, input_tokens + output_tokens)

    @classmethod
    def zero(cls) -> "Usage":
        """Returned by extract_usage() when a provider response is
        malformed or missing usage data. Deliberately not an exception:
        a billing bug from an unhandled crash here is worse than a
        logged zero-usage event the ledger can flag for review."""
        return cls(0, 0, 0)


class ProviderError(Exception):
    """Raised by an adapter's send() on a failed upstream call
    (timeout, non-2xx, connection error). The gateway catches this
    at one place rather than each adapter defining its own error type."""


@runtime_checkable
class ProviderAdapter(Protocol):
    """Implemented once per upstream provider. The gateway, quota check,
    ledger, and anomaly detector depend only on this interface — never
    on a concrete adapter or a provider's raw schema."""

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        """Convert an InternalRequest into the exact payload this
        provider's API expects."""
        ...

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        """Make the actual upstream call. Async so the gateway's event
        loop is never blocked waiting on a slow provider. Raises
        ProviderError on failure rather than returning a malformed
        ProviderResponse."""
        ...

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        """Normalize whatever usage fields this provider reports.
        Must not raise on missing/malformed usage data — return
        Usage.zero() and let the caller decide how to handle it."""
        ...

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        """Best-effort upper bound on tokens this request could consume.
        Used to reserve budget before a streaming response completes
        (see streaming.py). Overestimate rather than underestimate —
        the reservation is trued up against real usage once the stream
        closes, so an overestimate just means a temporary over-reserve,
        not a lost charge."""
        ...
    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yields raw provider chunks for a streaming request. Separate
        from send() rather than a flag on it, because a stream is
        fundamentally a different shape (async generator vs. single
        awaited value) — forcing both into one method signature was the
        wrong fit, which is why send() explicitly rejected stream=True."""
        ...

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        """Normalizes usage from the final chunk of a stream. Requires
        the provider to actually include usage on the last chunk (see
        stream_options in openai_compatible.py) — returns Usage.zero()
        if it's missing, same contract as extract_usage()."""
        ...