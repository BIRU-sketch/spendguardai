"""
SpendGuard Data Models & Types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Normalized token accounting for a completion."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TokenUsage":
        if not data or not isinstance(data, dict):
            return cls()
        p = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
        c = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
        t = int(data.get("total_tokens") or (p + c))

        cached = 0
        details = data.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)

        reasoning = 0
        comp_details = data.get("completion_tokens_details")
        if isinstance(comp_details, dict):
            reasoning = int(comp_details.get("reasoning_tokens") or 0)

        return cls(
            prompt_tokens=p,
            completion_tokens=c,
            total_tokens=t,
            cached_tokens=cached,
            reasoning_tokens=reasoning,
        )


@dataclass
class ChatResponse:
    """Convenient, strongly-typed response from SpendGuard gateway."""

    content: str | None
    model: str
    finish_reason: str | None
    usage: TokenUsage
    cost_cents: int = 0
    run_id: str | None = None
    agent_id: str | None = None
    run_step: int | None = None
    run_spent_cents: int | None = None
    run_remaining_cents: int | None = None
    tool_calls: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        """Cost of this specific completion in US dollars."""
        return self.cost_cents / 100.0

    @property
    def run_spent_usd(self) -> float | None:
        """Total spent across this agent run in US dollars."""
        return (self.run_spent_cents / 100.0) if self.run_spent_cents is not None else None

    @property
    def run_remaining_usd(self) -> float | None:
        """Remaining run budget in US dollars."""
        return (self.run_remaining_cents / 100.0) if self.run_remaining_cents is not None else None

    def __str__(self) -> str:
        return self.content or ""


@dataclass
class RunStatus:
    """Current state and metrics of an agent trajectory."""

    run_id: str
    project_id: str
    agent_id: str | None = None
    session_id: str | None = None
    budget_cents: int | None = None
    spent_cents: int = 0
    remaining_cents: int | None = None
    step_count: int = 0
    is_aborted: bool = False
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def budget_usd(self) -> float | None:
        return (self.budget_cents / 100.0) if self.budget_cents is not None else None

    @property
    def spent_usd(self) -> float:
        return self.spent_cents / 100.0

    @property
    def remaining_usd(self) -> float | None:
        return (self.remaining_cents / 100.0) if self.remaining_cents is not None else None


@dataclass(frozen=True)
class ModelInfo:
    """Pricing information for a supported model."""

    id: str
    owned_by: str
    input_cents_per_1k: float
    output_cents_per_1k: float
    currency: str = "USD"

    @property
    def input_usd_per_1m(self) -> float:
        return (self.input_cents_per_1k / 100.0) * 1000.0

    @property
    def output_usd_per_1m(self) -> float:
        return (self.output_cents_per_1k / 100.0) * 1000.0
