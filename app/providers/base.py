"""
ProviderAdapter interface — the boundary between SpendGuard's core logic
(quota checks, ledger, anomaly detection) and any specific LLM provider.

Nothing outside app/providers/ should ever see a provider's raw request
or response shape. Everything upstream talks to InternalRequest and Usage only.
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
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    top_p: float | None = None
    # Anything provider-specific that doesn't need normalization
    # (presence_penalty, stop, etc.) passes through here untouched.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResponse:
    """Raw or normalized response from the upstream provider."""

    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    """Normalized token usage. Every adapter must produce this shape
    regardless of how its provider names or splits the fields."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_input_output(
        cls,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> "Usage":
        """Convenience factory that derives total_tokens automatically."""
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    @classmethod
    def zero(cls) -> "Usage":
        """Returned when a response is malformed or missing usage data."""
        return cls(0, 0, 0, 0, 0)


class ProviderError(Exception):
    """Raised by an adapter on a failed upstream call (timeout, non-2xx, connection error)."""

    def __init__(self, message: str, status_code: int | None = None, provider: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider


@runtime_checkable
class ProviderAdapter(Protocol):
    """Implemented once per upstream provider. Gateway, reservation,
    ledger, and runaway detector depend only on this interface."""

    def translate_request(self, internal_req: InternalRequest) -> dict[str, Any]:
        """Convert an InternalRequest into the payload this provider expects."""
        ...

    async def send(self, provider_payload: dict[str, Any]) -> ProviderResponse:
        """Make the non-streaming upstream call."""
        ...

    async def stream(self, provider_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yields chunks for a streaming request. Guarantees usage is captured."""
        ...

    def extract_usage(self, provider_response: ProviderResponse) -> Usage:
        """Normalize token usage from a non-streaming response."""
        ...

    def extract_stream_usage(self, last_chunk: dict[str, Any]) -> Usage:
        """Normalize token usage from the stream's final/usage chunk."""
        ...

    def estimate_max_tokens(self, internal_req: InternalRequest) -> int:
        """Upper bound on tokens for pre-flight budget reservation."""
        ...
