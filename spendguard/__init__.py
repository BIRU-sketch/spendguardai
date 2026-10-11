"""
SpendGuard — Agent-Native Hard Cost Control Layer SDK.
"""

from spendguard.client import SpendGuard, AsyncSpendGuard
from spendguard.run import Run, AsyncRun
from spendguard.types import (
    ChatResponse,
    RunStatus,
    TokenUsage,
    ModelInfo,
)
from spendguard.errors import (
    SpendGuardError,
    AuthenticationError,
    BudgetExceededError,
    RunawayLoopError,
    RunAbortedError,
    ProviderError,
    APIError,
)
from spendguard.wrappers.openai import wrap_openai_client

__version__ = "2.0.0"

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
]
