"""
Pre-flight quota check and circuit breaker.

QuotaStore is an interface (same pattern as APIKeyStore) so main.py
depends on "check and reserve balance" without caring whether that's
backed by Redis or plain memory. InMemoryQuotaStore is today's stopgap
while Redis/WSL gets sorted out — RedisQuotaStore stays in this file,
unused for now, so switching back later is a one-line change in main.py,
not rewriting this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import redis.asyncio as redis


class QuotaExceeded(Exception):
    """Raised when a project has no balance left. Caught in main.py
    and turned into a 429 — the adapter must never be called once
    this is raised."""


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    remaining_cents: int


@runtime_checkable
class QuotaStore(Protocol):
    async def set_balance(self, project_id: str, cents: int) -> None: ...
    async def check_and_reserve(self, project_id: str, estimated_cost_cents: int) -> QuotaCheckResult: ...
    async def refund(self, project_id: str, cents: int) -> None: ...


class InMemoryQuotaStore:
    """Today's stopgap while Redis/WSL is unresolved. Two real
    limitations, worth knowing rather than discovering by surprise:
    balances vanish on every restart (including uvicorn --reload
    picking up a code change), and this won't work correctly if you
    ever run more than one worker process, since each process gets its
    own dict. Neither matters for single-process local testing."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def set_balance(self, project_id: str, cents: int) -> None:
        async with self._lock:
            self._balances[project_id] = cents

    async def check_and_reserve(self, project_id: str, estimated_cost_cents: int) -> QuotaCheckResult:
        async with self._lock:
            current = self._balances.get(project_id, 0)
            remaining = current - estimated_cost_cents

            if remaining < 0:
                return QuotaCheckResult(allowed=False, remaining_cents=current)

            self._balances[project_id] = remaining
            return QuotaCheckResult(allowed=True, remaining_cents=remaining)

    async def refund(self, project_id: str, cents: int) -> None:
        async with self._lock:
            self._balances[project_id] = self._balances.get(project_id, 0) + cents


class RedisQuotaStore:
    """Real implementation, unused until Redis is actually reachable.
    Kept here rather than deleted so swapping back is changing one
    line in main.py, not rewriting this class from scratch."""

    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)

    def _key(self, project_id: str) -> str:
        return f"project:{project_id}:balance_cents"

    async def set_balance(self, project_id: str, cents: int) -> None:
        await self._client.set(self._key(project_id), cents)

    async def check_and_reserve(self, project_id: str, estimated_cost_cents: int) -> QuotaCheckResult:
        key = self._key(project_id)
        remaining = await self._client.decrby(key, estimated_cost_cents)

        if remaining < 0:
            await self._client.incrby(key, estimated_cost_cents)
            return QuotaCheckResult(allowed=False, remaining_cents=remaining + estimated_cost_cents)

        return QuotaCheckResult(allowed=True, remaining_cents=remaining)

    async def refund(self, project_id: str, cents: int) -> None:
        await self._client.incrby(self._key(project_id), cents)