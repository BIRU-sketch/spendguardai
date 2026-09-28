"""
billing.py — background worker for:

  A) BillingLineItem    → batch-writes to billing_line_items (reuses the
                          queue + worker pattern from ledger.py).
  B) Payout accounts    → simple CRUD around payout_accounts table.
  C) End user upsert    → upsert end_users dim table on first sighting.
  D) payment_events     → idempotency journal + write-on-receive.

Called from main.py's request path (A, C) and from the webhook handler (D).
The payout_accounts methods are used only during developer onboarding
(infrequent), so no queue needed there — synchronous DB calls are fine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BillingLineEvent:
    event_id: str
    project_id: str
    end_user_id: str | None
    ledger_event_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    sg_cost_cents: Decimal
    end_user_cost_cents: Decimal
    pricing_tier: str
    input_cost_cents: Decimal
    output_cost_cents: Decimal
    flat_fee_cost_cents: Decimal
    currency: str                     # ISO 4217 CHAR(3)
    processor_charge_id: str | None


@dataclass(frozen=True)
class PayoutAccount:
    """payout_accounts row — processor-agnostic.

    For Chapa: processor='chapa', processor_account_id=Chapa subaccount id,
               bank_code/account_number/account_name populated from the
               founder's bank details, split_type/split_value applied by
               Chapa on each tx via `subaccounts: {id: ...}`.
    For Stripe later: processor='stripe', processor_account_id=acct_xxx,
               bank fields NULL, split stored separately or on Stripe side.
    """
    project_id: str
    processor: str
    processor_account_id: str
    bank_code: str
    account_number: str
    account_name: str
    split_type: str          # 'percentage' | 'flat'
    split_value: Decimal
    onboarding_state: str    # 'pending' | 'complete' | 'rejected'
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# BillingWorker
# ---------------------------------------------------------------------------

class BillingWorker:
    """Queue-based batch-writer to billing_line_items — pattern matches
    app/ledger.py Ledger class exactly.  Accepts BillingLineEvent
    instantly; a background task drains and batch-writes every 1s."""

    def __init__(
        self,
        database_url: str,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self._database_url = database_url
        self._flush_interval = flush_interval_seconds
        self._queue: asyncio.Queue[BillingLineEvent] = asyncio.Queue()
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

    def record_line_item(
        self,
        *,
        project_id: str,
        end_user_id: str | None,
        ledger_event_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        sg_cost_cents: Decimal,
        end_user_cost_cents: Decimal,
        pricing_tier: str = "default",
        input_cost_cents: Decimal | None = None,
        output_cost_cents: Decimal | None = None,
        flat_fee_cost_cents: Decimal | None = None,
        currency: str = "ETB",
        processor_charge_id: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event = BillingLineEvent(
            event_id=event_id,
            project_id=project_id,
            end_user_id=end_user_id,
            ledger_event_id=ledger_event_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            sg_cost_cents=sg_cost_cents,
            end_user_cost_cents=end_user_cost_cents,
            pricing_tier=pricing_tier,
            input_cost_cents=input_cost_cents or Decimal("0"),
            output_cost_cents=output_cost_cents or Decimal("0"),
            flat_fee_cost_cents=flat_fee_cost_cents or Decimal("0"),
            currency=currency.upper(),
            processor_charge_id=processor_charge_id,
        )
        self._queue.put_nowait(event)
        return event_id

    async def _run_worker(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        batch: list[BillingLineEvent] = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if not batch:
            return
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO billing_line_items
                    (id, project_id, end_user_id, ledger_event_id, model,
                     input_tokens, output_tokens, total_tokens,
                     sg_cost_cents, end_user_cost_cents, currency,
                     pricing_tier,
                     input_cost_cents, output_cost_cents,
                     flat_fee_cost_cents, processor_charge_id, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, 'settled')
                ON CONFLICT (id) DO NOTHING
                """,
                [
                    (
                        uuid.UUID(e.event_id),
                        e.project_id,
                        e.end_user_id,
                        e.ledger_event_id,
                        e.model,
                        e.input_tokens,
                        e.output_tokens,
                        e.total_tokens,
                        float(e.sg_cost_cents),
                        float(e.end_user_cost_cents),
                        e.currency,
                        e.pricing_tier,
                        float(e.input_cost_cents),
                        float(e.output_cost_cents),
                        float(e.flat_fee_cost_cents),
                        e.processor_charge_id,
                    )
                    for e in batch
                ],
            )


# ---------------------------------------------------------------------------
# Payout accounts CRUD (was: ConnectAccountStore → now PayoutAccountStore)
# ---------------------------------------------------------------------------

class PayoutAccountStore:
    """Simple CRUD around payout_accounts.  No queueing — this is admin-
    frequency, not request-frequency."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url)

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    def _require(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PayoutAccountStore not started")
        return self._pool

    async def get(self, project_id: str) -> PayoutAccount | None:
        row = await self._require().fetchrow(
            "SELECT * FROM payout_accounts WHERE project_id = $1",
            project_id,
        )
        return self._row(row) if row else None

    async def upsert(
        self,
        *,
        project_id: str,
        processor: str,
        processor_account_id: str,
        bank_code: str,
        account_number: str,
        account_name: str,
        split_type: str,
        split_value: Decimal,
        onboarding_state: str = "pending",
    ) -> PayoutAccount:
        if split_type not in ("percentage", "flat"):
            raise ValueError(
                f"split_type must be 'percentage' or 'flat', got {split_type!r}"
            )
        if onboarding_state not in ("pending", "complete", "rejected"):
            raise ValueError(
                f"onboarding_state must be pending/complete/rejected, got {onboarding_state!r}"
            )
        await self._require().execute(
            """
            INSERT INTO payout_accounts
                (project_id, processor, processor_account_id,
                 bank_code, account_number, account_name,
                 split_type, split_value, onboarding_state,
                 created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            ON CONFLICT (project_id) DO UPDATE SET
                processor = EXCLUDED.processor,
                processor_account_id = EXCLUDED.processor_account_id,
                bank_code = EXCLUDED.bank_code,
                account_number = EXCLUDED.account_number,
                account_name = EXCLUDED.account_name,
                split_type = EXCLUDED.split_type,
                split_value = EXCLUDED.split_value,
                onboarding_state = EXCLUDED.onboarding_state,
                updated_at = NOW()
            """,
            project_id, processor, processor_account_id,
            bank_code, account_number, account_name,
            split_type, split_value, onboarding_state,
        )
        got = await self.get(project_id)
        if got is None:
            raise RuntimeError("Payout account missing after upsert")
        return got

    async def mark_state(
        self,
        project_id: str,
        onboarding_state: str,
    ) -> PayoutAccount | None:
        row = await self._require().fetchrow(
            """
            UPDATE payout_accounts
            SET onboarding_state = $1, updated_at = NOW()
            WHERE project_id = $2
            RETURNING *
            """,
            onboarding_state, project_id,
        )
        return self._row(row) if row else None

    async def mark_state_by_processor_account(
        self,
        processor: str,
        processor_account_id: str,
        onboarding_state: str,
    ) -> PayoutAccount | None:
        """Useful for processor-side webhooks that send subaccount updates
        (Stripe account.updated, future Chapa subaccount.status events)."""
        row = await self._require().fetchrow(
            """
            UPDATE payout_accounts
            SET onboarding_state = $1, updated_at = NOW()
            WHERE processor = $2 AND processor_account_id = $3
            RETURNING *
            """,
            onboarding_state, processor, processor_account_id,
        )
        return self._row(row) if row else None

    @staticmethod
    def _row(r: asyncpg.Record) -> PayoutAccount:
        return PayoutAccount(
            project_id=r["project_id"],
            processor=r["processor"],
            processor_account_id=r["processor_account_id"],
            bank_code=r["bank_code"],
            account_number=r["account_number"],
            account_name=r["account_name"],
            split_type=r["split_type"],
            split_value=Decimal(str(r["split_value"])),
            onboarding_state=r["onboarding_state"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


# ---------------------------------------------------------------------------
# End user dim-table upsert + payment_events journal writes
# ---------------------------------------------------------------------------

class EndUserStore:
    """Lightweight dim-table upsert.  Called on every request that has an
    end_user_id so operator dashboards always have display_name / first_
    seen / last_seen tracking."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url)

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    async def touch(
        self,
        project_id: str,
        end_user_id: str,
        metadata: dict[str, Any] | None = None,
        display_name: str | None = None,
    ) -> None:
        assert self._pool is not None
        merged_metadata: dict[str, Any] = metadata if metadata else {}
        await self._pool.execute(
            """
            INSERT INTO end_users (project_id, end_user_id, display_name, metadata)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (project_id, end_user_id) DO UPDATE SET
                last_seen_at = NOW(),
                display_name = COALESCE(EXCLUDED.display_name, end_users.display_name),
                metadata = end_users.metadata || EXCLUDED.metadata
            """,
            project_id, end_user_id, display_name,
            json.dumps(merged_metadata),
        )


class PaymentEventJournal:
    """Write-on-receive + idempotency for ANY processor (Chapa today,
    Stripe tomorrow).  Webhook handler calls:

       1. record(event_id, processor, type, processor_account_id, payload)
            → returns True if first delivery (process now)
            → returns False if already seen (skip, ack HTTP 200)
       2. mark_delivered(event_id [, error])
            → after process() succeeds or definitively fails.

    At-least-once delivery from the processor becomes exactly-once on
    our side."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url)

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    async def record(
        self,
        *,
        processor_event_id: str,
        processor: str,
        event_type: str,
        processor_account_id: str | None,
        raw_payload: dict[str, Any] | str,
    ) -> bool:
        """Return True if this is the FIRST delivery.  False = already
        seen, skip processing.  For processors that don't provide a
        canonical event id (some just POST JSON to a URL), callers can
        derive one via sha256(processor + raw_json_bytes) — that's
        stable per-event even if the processor retries with identical
        body."""
        assert self._pool is not None
        if isinstance(raw_payload, (dict, list)):
            raw_bytes = json.dumps(raw_payload, sort_keys=True).encode("utf-8")
            jsonb_val = json.dumps(raw_payload)
        else:
            raw_bytes = raw_payload.encode("utf-8") if isinstance(raw_payload, str) else bytes(raw_payload)
            jsonb_val = raw_payload if isinstance(raw_payload, str) else raw_payload.decode("utf-8")
        try:
            await self._pool.execute(
                """
                INSERT INTO payment_events
                    (processor_event_id, processor, event_type,
                     processor_account_id, raw_payload)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                processor_event_id, processor.lower(), event_type,
                processor_account_id, jsonb_val,
            )
        except asyncpg.UniqueViolationError:
            return False
        return True

    async def mark_delivered(
        self,
        processor_event_id: str,
        error: str | None = None,
    ) -> None:
        assert self._pool is not None
        now = datetime.now(timezone.utc)
        if error:
            await self._pool.execute(
                "UPDATE payment_events SET last_error = $1 WHERE processor_event_id = $2",
                error, processor_event_id,
            )
        else:
            await self._pool.execute(
                """
                UPDATE payment_events
                SET delivered = TRUE, delivered_at = $1
                WHERE processor_event_id = $2
                """,
                now, processor_event_id,
            )
