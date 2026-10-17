"""
sg-run — Agent-Native Hard Cost Control Layer SDK.
"""

from spendguard import (
    SpendGuard,
    AsyncSpendGuard,
    Run,
    AsyncRun,
    ChatResponse,
    RunStatus,
    TokenUsage,
    ModelInfo,
    SpendGuardError,
    AuthenticationError,
    BudgetExceededError,
    RunawayLoopError,
    RunAbortedError,
    ProviderError,
    APIError,
    wrap_openai_client,
    __version__,
)

__all__ = [
    "SpendGuard",
    "AsyncSpendGuard",
    "Run",
    "AsyncRun",
    "ChatResponse",
    "RunStatus",
    "TokenUsage",
    "ModelInfo",
    "SpendGuardError",
    "AuthenticationError",
    "BudgetExceededError",
    "RunawayLoopError",
    "RunAbortedError",
    "ProviderError",
    "APIError",
    "wrap_openai_client",
    "__version__",
]
