"""
SpendGuard SDK Exceptions.
"""

from __future__ import annotations

from typing import Any


class SpendGuardError(Exception):
    """Base exception for all SpendGuard SDK errors."""

    def __init__(self, message: str, status_code: int | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class AuthenticationError(SpendGuardError):
    """Raised when an API key is invalid, missing, or revoked."""


class BudgetExceededError(SpendGuardError):
    """Raised when an agent run ceiling or workspace balance is breached (HTTP 429)."""

    def __init__(
        self,
        message: str,
        scope: str = "run_budget",
        reason: str = "run_budget_exhausted",
        run_id: str | None = None,
        spent_cents: int = 0,
        budget_cents: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=429, details=details)
        self.scope = scope
        self.reason = reason
        self.run_id = run_id
        self.spent_cents = spent_cents
        self.budget_cents = budget_cents

    @property
    def spent_usd(self) -> float:
        return self.spent_cents / 100.0

    @property
    def budget_usd(self) -> float | None:
        return (self.budget_cents / 100.0) if self.budget_cents is not None else None


class RunawayLoopError(SpendGuardError):
    """Raised when an autonomous agent triggers runaway loop protection (HTTP 429)."""

    def __init__(
        self,
        message: str,
        reason: str = "runaway_loop",
        run_id: str | None = None,
        step_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=429, details=details)
        self.reason = reason
        self.run_id = run_id
        self.step_count = step_count


class RunAbortedError(SpendGuardError):
    """Raised when an agent run was halted by an operator kill switch (HTTP 429)."""

    def __init__(self, message: str, run_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=429, details=details)
        self.run_id = run_id


class ProviderError(SpendGuardError):
    """Raised when an upstream LLM provider fails or times out (HTTP 502)."""


class APIError(SpendGuardError):
    """General gateway API error."""
