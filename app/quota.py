"""
Quota store and low-level Redis reservation primitive.

Provides atomic reservation, deduction, and refund operations on Redis counters.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import redis.asyncio as redis


# Starter credits per project tier in USD cents ($10 for free, $50 for builder, etc.)
DEFAULT_TIER_QUOTAS_CENTS: dict[str, int] = {
    "free": 1000,           # $10.00 USD starter credit
    "builder": 5000,        # $50.00 USD
    "paid": 5000,           # $50.00 USD
    "team": 20000,          # $200.00 USD
    "enterprise": 100000,   # $1,000.00 USD
    "default": 1000,
}


def default_quota_for_tier(tier: str | None) -> int:
    """Returns the default initial quota in cents for a given tier."""
    if not tier:
        return DEFAULT_TIER_QUOTAS_CENTS["default"]
    return DEFAULT_TIER_QUOTAS_CENTS.get(
        tier.strip().lower(), DEFAULT_TIER_QUOTAS_CENTS["default"]
    )


class QuotaExceeded(Exception):
    """Raised when a balance or budget has been exceeded."""


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    remaining_cents: int


@runtime_checkable
class QuotaStore(Protocol):
    async def set_balance(self, project_id: str, cents: int) -> None: ...
    async def get_balance(
        self, project_id: str, default_cents: int = 1000
    ) -> int: ...
    async def check_and_reserve(
        self,
        project_id: str,
        estimated_cost_cents: int,
        default_cents: int = 1000,
    ) -> QuotaCheckResult: ...
    async def refund(self, project_id: str, cents: int) -> None: ...


class InMemoryQuotaStore:
    """In-memory store for local testing and isolated unit tests."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def set_balance(self, project_id: str, cents: int) -> None:
        async with self._lock:
            self._balances[project_id] = cents

    async def get_balance(self, project_id: str, default_cents: int = 1000) -> int:
        async with self._lock:
            if project_id not in self._balances:
                self._balances[project_id] = default_cents
            return self._balances[project_id]

    async def check_and_reserve(
        self,
        project_id: str,
        estimated_cost_cents: int,
        default_cents: int = 1000,
    ) -> QuotaCheckResult:
        async with self._lock:
            if project_id not in self._balances:
                self._balances[project_id] = default_cents
            current = self._balances[project_id]
            remaining = current - estimated_cost_cents

            if remaining < 0:
                return QuotaCheckResult(allowed=False, remaining_cents=current)

            self._balances[project_id] = remaining
            return QuotaCheckResult(allowed=True, remaining_cents=remaining)

    async def refund(self, project_id: str, cents: int) -> None:
        async with self._lock:
            self._balances[project_id] = self._balances.get(project_id, 0) + cents


class RedisQuotaStore:
    """Atomic Redis-backed quota and reservation store."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: redis.Redis | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _get_client(self) -> redis.Redis:
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None
        if self._client is None or (
            self._client_loop is not None and self._client_loop != cur_loop
        ):
            self._client = redis.from_url(self._redis_url, decode_responses=True)
            self._client_loop = cur_loop
        return self._client

    def _key(self, project_id: str) -> str:
        return f"project:{project_id}:balance_cents"

    async def set_balance(self, project_id: str, cents: int) -> None:
        await self._get_client().set(self._key(project_id), cents)

    async def get_balance(self, project_id: str, default_cents: int = 1000) -> int:
        client = self._get_client()
        key = self._key(project_id)
        val = await client.get(key)
        if val is None:
            await client.setnx(key, default_cents)
            return default_cents
        return int(val)

    async def check_and_reserve(
        self,
        project_id: str,
        estimated_cost_cents: int,
        default_cents: int = 1000,
    ) -> QuotaCheckResult:
        client = self._get_client()
        key = self._key(project_id)
        # Atomically initialize starter balance if key doesn't exist yet
        await client.setnx(key, default_cents)
        remaining = await client.decrby(key, estimated_cost_cents)

        if remaining < 0:
            await client.incrby(key, estimated_cost_cents)
            return QuotaCheckResult(
                allowed=False, remaining_cents=remaining + estimated_cost_cents
            )

        return QuotaCheckResult(allowed=True, remaining_cents=remaining)

    async def refund(self, project_id: str, cents: int) -> None:
        if cents <= 0:
            return
        await self._get_client().incrby(self._key(project_id), cents)
