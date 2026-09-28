"""
billing_plans — CRUD around the DB billing_plans table + optional
processor-side Price registration (Stripe has prices; Chapa doesn't — for
Chapa, charge_amount lives directly ON the plan row).

RULES ENFORCED HERE:
  1. A plan has ONE processor_price_id (nullable — NULL for Chapa-style
     processors that don't have a pre-registered Price concept).
  2. The plan's usage_allocation_cents + currency MUST be ≤
     charge_amount_cents + same currency (DB CHECK + app-level check).
  3. HARD SPLIT VALIDATION: Given the project's payout_accounts
     split_type/split_value, compute founder_payout and platform_remainder
     and REJECT the plan if usage_allocation > platform_remainder.  This
     is NOT a warning and NOT a checkout-time check — it runs on every
     create/update that touches any of (charge, usage_alloc, split).
     Error body exposes the 4 numbers so the operator can fix it.
  4. founder_payout is NEVER stored — derived, always.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from app.pricing import (
    Money,
    PayoutSplit,
    validate_billing_plan_split,
)


@dataclass(frozen=True)
class BillingPlan:
    project_id: str
    plan_id: str
    display_name: str
    description: str | None
    processor_price_id: str | None        # NULL for Chapa-style processors
    charge: Money                         # charge_amount + currency
    usage_allocation: Money               # usage_allocation_cents + currency
    expiry_days: int | None
    is_active: bool
    sort_order: int

    @property
    def charge_amount_cents(self) -> int: return self.charge.cents
    @property
    def usage_allocation_cents(self) -> int: return self.usage_allocation.cents
    @property
    def currency(self) -> str: return self.charge.currency


class BillingPlanStore:
    """Wraps billing_plans + payout_accounts join for split validation."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url)

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("BillingPlanStore not started")
        return self._pool

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_plan(row: asyncpg.Record) -> BillingPlan:
        currency = row["currency"] or "ETB"
        return BillingPlan(
            project_id=row["project_id"],
            plan_id=row["plan_id"],
            display_name=row["display_name"],
            description=row["description"],
            processor_price_id=row.get("processor_price_id"),
            charge=Money(int(row["charge_amount_cents"]), currency),
            usage_allocation=Money(int(row["usage_allocation_cents"]), currency),
            expiry_days=row["expiry_days"],
            is_active=bool(row["is_active"]),
            sort_order=int(row["sort_order"]),
        )

    async def _fetch_payout_split(
        self,
        project_id: str,
        *,
        plan_currency: str,
    ) -> PayoutSplit | None:
        """Return the project's payout split (currency-matched to the plan)
        from payout_accounts, or None if no payout account exists yet.
        Caller decides whether to proceed (we need split info for the
        hard validation, so missing payout_account = reject until one
        exists — see create/update)."""
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT split_type, split_value FROM payout_accounts WHERE project_id = $1",
            project_id,
        )
        if row is None:
            return None
        return PayoutSplit(
            split_type=str(row["split_type"]),
            split_value=Decimal(str(row["split_value"])),
            currency=plan_currency,  # flat split lives in same cur as charges
        )

    # ------------------------------------------------------------------
    # CRUD (plan metadata + charge amount + currency)
    # ------------------------------------------------------------------

    async def list(self, project_id: str, include_archived: bool = False) -> list[BillingPlan]:
        pool = self._require_pool()
        if include_archived:
            rows = await pool.fetch(
                "SELECT * FROM billing_plans WHERE project_id = $1 ORDER BY sort_order, plan_id",
                project_id,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM billing_plans WHERE project_id = $1 AND is_active = TRUE ORDER BY sort_order, plan_id",
                project_id,
            )
        return [self._row_to_plan(r) for r in rows]

    async def get(self, project_id: str, plan_id: str) -> BillingPlan | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM billing_plans WHERE project_id = $1 AND plan_id = $2",
            project_id, plan_id,
        )
        return self._row_to_plan(row) if row else None

    async def create(
        self,
        *,
        project_id: str,
        plan_id: str,
        display_name: str,
        description: str | None,
        charge_amount_cents: int,
        usage_allocation_cents: int,
        currency: str = "ETB",
        expiry_days: int | None = None,
        sort_order: int = 0,
        processor_price_id: str | None = None,
    ) -> BillingPlan:
        # Basic value sanity
        if charge_amount_cents < 0:
            raise ValueError(f"charge_amount_cents negative: {charge_amount_cents}")
        if usage_allocation_cents < 0:
            raise ValueError("usage_allocation_cents cannot be negative")
        cur = currency.upper()
        charge = Money(charge_amount_cents, cur)
        alloc = Money(usage_allocation_cents, cur)

        # HARD VALIDATION: project payout split must already exist, and
        # usage_allocation ≤ platform_remainder.  If there's no payout
        # account yet, we refuse to create a plan (we can't guarantee no
        # over-commit).
        split = await self._fetch_payout_split(project_id, plan_currency=cur)
        if split is None:
            raise ValueError(
                f"No payout_account configured for project {project_id!r}. "
                f"A project must complete its payout/onboarding first "
                f"(POST /billing/payout/onboarding) before creating plans, "
                f"because plan validation needs the founder split math."
            )
        res = validate_billing_plan_split(
            charge_amount=charge,
            usage_allocation=alloc,
            payout_split=split,
        )
        if not res.ok:
            raise ValueError(res.error_message or "Plan validation failed")

        pool = self._require_pool()
        await pool.execute(
            """
            INSERT INTO billing_plans
                (project_id, plan_id, display_name, description,
                 processor_price_id, charge_amount_cents, currency,
                 usage_allocation_cents, expiry_days, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            project_id, plan_id, display_name, description,
            processor_price_id, charge_amount_cents, cur,
            usage_allocation_cents, expiry_days, sort_order,
        )
        plan = await self.get(project_id, plan_id)
        if plan is None:
            raise RuntimeError("Plan not found immediately after insert")
        return plan

    async def update(
        self,
        project_id: str,
        plan_id: str,
        **fields: Any,
    ) -> BillingPlan | None:
        allowed = {
            "display_name", "description", "processor_price_id",
            "charge_amount_cents", "currency", "usage_allocation_cents",
            "expiry_days", "is_active", "sort_order",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Unknown billing_plan fields: {invalid}")
        if not fields:
            return await self.get(project_id, plan_id)

        existing = await self.get(project_id, plan_id)
        if existing is None:
            return None

        # Compute effective (charge, usage_alloc, currency) AFTER update
        # so we can re-run the HARD SPLIT VALIDATION.
        cur = (fields.get("currency") or existing.currency).upper()
        charge_cents = int(fields.get("charge_amount_cents", existing.charge_amount_cents))
        alloc_cents = int(fields.get("usage_allocation_cents", existing.usage_allocation_cents))
        charge = Money(charge_cents, cur)
        alloc = Money(alloc_cents, cur)

        split = await self._fetch_payout_split(project_id, plan_currency=cur)
        if split is None:
            raise ValueError(
                "Cannot update plan: no payout_account configured for project. "
                "Complete the payout onboarding first (POST /billing/payout/onboarding)."
            )
        res = validate_billing_plan_split(
            charge_amount=charge,
            usage_allocation=alloc,
            payout_split=split,
        )
        if not res.ok:
            raise ValueError(res.error_message or "Plan validation failed")

        assignments = []
        args: list[Any] = []
        idx = 1
        for k, v in fields.items():
            if k == "currency" and isinstance(v, str):
                v = v.upper()
            assignments.append(f"{k} = ${idx}")
            args.append(v)
            idx += 1
        args.extend([project_id, plan_id])
        sql = (
            f"UPDATE billing_plans SET {', '.join(assignments)}, updated_at = NOW() "
            f"WHERE project_id = ${idx} AND plan_id = ${idx + 1}"
        )
        pool = self._require_pool()
        await pool.execute(sql, *args)
        return await self.get(project_id, plan_id)
