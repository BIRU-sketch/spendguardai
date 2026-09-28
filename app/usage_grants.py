"""
usage_grants — the source of truth for an end-user's spendable AI balance.

CHANGES from the Stripe draft:
  • Every money column (granted_cents, remaining_cents) now has a
    currency CHAR(3) partner.
  • The hot-path queries (list_active_grants, total_remaining_cents,
    consume_fifo) are now per-currency; summing across different
    currencies raises ValueError instead of silently adding ETB to USD.
  • grant_cents() accepts an explicit currency param (default ETB).

FIFO consumption, expiry sweep, and worker cadence — identical.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import asyncpg

from app.pricing import Money, sum_money


@dataclass(frozen=True)
class Grant:
    id: str
    project_id: str
    end_user_id: str
    plan_id: str | None
    granted: Money
    remaining: Money

    @property
    def granted_cents(self) -> int: return self.granted.cents
    @property
    def remaining_cents(self) -> int: return self.remaining.cents
    @property
    def currency(self) -> str: return self.granted.currency
    @property
    def expires_at(self) -> datetime | None: return self._expires_at  # type: ignore[attr-defined]

    def __init__(
        self,
        *,
        id: str,
        project_id: str,
        end_user_id: str,
        plan_id: str | None,
        granted_cents: int,
        remaining_cents: int,
        currency: str,
        expires_at: datetime | None,
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "end_user_id", end_user_id)
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "granted", Money(granted_cents, currency))
        object.__setattr__(self, "remaining", Money(remaining_cents, currency))
        object.__setattr__(self, "_expires_at", expires_at)

    @property
    def is_active(self, now: datetime | None = None) -> bool:
        if self.remaining_cents <= 0:
            return False
        if self._expires_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return self._expires_at > now


@dataclass(frozen=True)
class ConsumeResult:
    allowed: bool
    consumed: Money
    remaining_total: Money
    grant_draws: list[tuple[str, int]]   # [(grant_id, cents_drawn), ...]
    reason: str | None = None

    @property
    def cents_consumed(self) -> int: return self.consumed.cents
    @property
    def remaining_total_cents(self) -> int: return self.remaining_total.cents
    @property
    def currency(self) -> str: return self.consumed.currency


class UsageGrantStore:
    def __init__(
        self,
        database_url: str,
        sweep_interval_seconds: float = 60.0,
    ) -> None:
        self._database_url = database_url
        self._sweep_interval = sweep_interval_seconds
        self._pool: asyncpg.Pool | None = None
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url)
        self._worker_task = asyncio.create_task(self._run_worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        if self._pool:
            await self._pool.close()

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("UsageGrantStore not started — call start() first")
        return self._pool

    # ------------------------------------------------------------------
    # grant_cents — primary ingress from webhook
    # ------------------------------------------------------------------

    async def grant_cents(
        self,
        *,
        project_id: str,
        end_user_id: str,
        cents: int,
        currency: str = "ETB",
        plan_id: str | None = None,
        source_type: str = "adjustment",
        source_reference: str | None = None,
        processor_charge_id: str | None = None,
        expiry_days: int | None = None,
    ) -> str:
        if cents <= 0:
            raise ValueError(f"Cannot grant non-positive cents: {cents}")
        pool = self._require_pool()
        expires_at = None
        if expiry_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)
        gid = str(uuid.uuid4())
        await pool.execute(
            """
            INSERT INTO usage_grants
                (id, project_id, end_user_id, plan_id, source_reference,
                 source_type, granted_cents, remaining_cents, currency,
                 expires_at, processor_charge_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            gid, project_id, end_user_id, plan_id, source_reference,
            source_type, cents, cents, currency.upper(), expires_at,
            processor_charge_id,
        )
        return gid

    # ------------------------------------------------------------------
    # list_active_grants + total_remaining_cents — both now PER-CURRENCY.
    # If no currency filter is given, total_remaining_cents refuses to
    # mix currencies (ValueError) so callers can't accidentally add ETB
    # and USD together.
    # ------------------------------------------------------------------

    async def list_active_grants(
        self,
        project_id: str,
        end_user_id: str,
        *,
        currency: str | None = None,
    ) -> list[Grant]:
        pool = self._require_pool()
        now = datetime.now(timezone.utc)
        if currency:
            rows = await pool.fetch(
                """
                SELECT id, project_id, end_user_id, plan_id,
                       granted_cents, remaining_cents, currency, expires_at
                FROM usage_grants
                WHERE project_id = $1 AND end_user_id = $2
                  AND currency = $3
                  AND remaining_cents > 0
                  AND (expires_at IS NULL OR expires_at > $4)
                ORDER BY created_at ASC
                """,
                project_id, end_user_id, currency.upper(), now,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT id, project_id, end_user_id, plan_id,
                       granted_cents, remaining_cents, currency, expires_at
                FROM usage_grants
                WHERE project_id = $1 AND end_user_id = $2
                  AND remaining_cents > 0
                  AND (expires_at IS NULL OR expires_at > $3)
                ORDER BY currency, created_at ASC
                """,
                project_id, end_user_id, now,
            )
        return [
            Grant(
                id=str(r["id"]),
                project_id=r["project_id"],
                end_user_id=r["end_user_id"],
                plan_id=r["plan_id"],
                granted_cents=int(r["granted_cents"]),
                remaining_cents=int(r["remaining_cents"]),
                currency=r["currency"],
                expires_at=r["expires_at"],
            )
            for r in rows
        ]

    async def total_remaining_cents(
        self,
        project_id: str,
        end_user_id: str,
        *,
        currency: str | None = None,
    ) -> int:
        """Sum(remaining) across active grants.  Currency-mixing guard:
        If currency=None and there are grants in >1 currency, raise."""
        grants = await self.list_active_grants(project_id, end_user_id)
        if not grants:
            return 0
        cur = currency.upper() if currency else None
        if cur is None:
            # Auto-detect single currency; multi → refuse
            distinct_cur = {g.currency for g in grants}
            if len(distinct_cur) > 1:
                raise ValueError(
                    f"total_remaining_cents({project_id}/{end_user_id}) "
                    f"found multiple currencies: {sorted(distinct_cur)}. "
                    f"Pass currency= to sum a specific one."
                )
            cur = grants[0].currency
        matched = [g.remaining for g in grants if g.currency == cur]
        return sum_money(matched, expected_currency=cur).cents

    async def total_remaining_money(
        self,
        project_id: str,
        end_user_id: str,
        *,
        currency: str | None = None,
    ) -> Money:
        """Same as total_remaining_cents but returns Money(cents, cur)."""
        cents = await self.total_remaining_cents(project_id, end_user_id, currency=currency)
        cur = currency.upper() if currency else "ETB"
        return Money(cents, cur)

    # ------------------------------------------------------------------
    # consume_fifo — FIFO draw.  Currency-aware: requested_cents must
    # come in a specific currency (we consume only grants of that
    # currency, never cross-currency draws).
    # ------------------------------------------------------------------

    async def consume_fifo(
        self,
        *,
        project_id: str,
        end_user_id: str,
        requested_cents: int,
        currency: str,
        billing_line_item_id: str | None,
    ) -> ConsumeResult:
        cur = currency.upper()
        if requested_cents <= 0:
            return ConsumeResult(
                allowed=True,
                consumed=Money(0, cur),
                remaining_total=await self.total_remaining_money(
                    project_id, end_user_id, currency=cur,
                ),
                grant_draws=[],
            )
        pool = self._require_pool()
        now = datetime.now(timezone.utc)

        grants = [g for g in await self.list_active_grants(
            project_id, end_user_id, currency=cur,
        )]
        available_cents = sum(g.remaining_cents for g in grants)
        if available_cents < requested_cents:
            return ConsumeResult(
                allowed=False,
                consumed=Money(0, cur),
                remaining_total=Money(available_cents, cur),
                grant_draws=[],
                reason="Insufficient grant balance",
            )

        draws: list[tuple[str, int]] = []
        remaining_draw = requested_cents
        now_ts = now

        async with pool.acquire() as conn:
            async with conn.transaction():
                for g in grants:
                    if remaining_draw <= 0:
                        break
                    draw = min(g.remaining_cents, remaining_draw)
                    if draw <= 0:
                        continue
                    new_remaining = g.remaining_cents - draw
                    result = await conn.fetchrow(
                        """
                        UPDATE usage_grants
                        SET remaining_cents = $1,
                            consumed_last_at = $2
                        WHERE id = $3 AND remaining_cents = $4
                          AND (expires_at IS NULL OR expires_at > $5)
                        RETURNING remaining_cents
                        """,
                        new_remaining, now_ts, uuid.UUID(g.id), g.remaining_cents, now_ts,
                    )
                    if result is None:
                        raise asyncio.PostgresError(
                            f"Concurrent update or expiry on grant {g.id}, retry"
                        )
                    draws.append((g.id, draw))
                    remaining_draw -= draw

                if remaining_draw > 0:
                    raise asyncio.PostgresError(
                        "Concurrent consumption race — retry"
                    )

                if billing_line_item_id:
                    bli_id = uuid.UUID(billing_line_item_id)
                    for grant_id, cents in draws:
                        await conn.execute(
                            """
                            INSERT INTO grant_consumptions
                                (grant_id, billing_line_item_id, cents_drawn)
                            VALUES ($1, $2, $3)
                            """,
                            uuid.UUID(grant_id), bli_id, cents,
                        )

        total_after = await self.total_remaining_money(
            project_id, end_user_id, currency=cur,
        )
        return ConsumeResult(
            allowed=True,
            consumed=Money(requested_cents, cur),
            remaining_total=total_after,
            grant_draws=draws,
        )

    # ------------------------------------------------------------------
    # Sweep worker — expires grants past their date.
    # ------------------------------------------------------------------

    async def _run_worker(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
                await self._sweep_once()
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(self._sweep_interval)

    async def _sweep_once(self) -> None:
        pool = self._require_pool()
        now = datetime.now(timezone.utc)
        await pool.execute(
            """
            UPDATE usage_grants
            SET remaining_cents = 0
            WHERE expires_at IS NOT NULL
              AND expires_at <= $1
              AND remaining_cents > 0
            """,
            now,
        )
