"""
Billing line item journaling — records detailed cost metrics for agent runs.

Reuses the queue + batch worker pattern to write to billing_line_items off the hot path.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal

import asyncpg


@dataclass(frozen=True)
class BillingLineEvent:
    event_id: str
    project_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_cents: Decimal
    input_cost_cents: Decimal
    output_cost_cents: Decimal
    flat_fee_cost_cents: Decimal
    currency: str = "USD"
    run_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    ledger_event_id: str | None = None
    end_user_id: str | None = None


class BillingWorker:
    """Async batch worker that writes line items into Postgres."""

    def __init__(
        self,
        database_url: str,
        flush_interval_seconds: float = 1.0,
        max_batch_size: int = 100,
    ) -> None:
        self._database_url = database_url
        self._flush_interval = flush_interval_seconds
        self._max_batch_size = max_batch_size
        self._queue: asyncio.Queue[BillingLineEvent] = asyncio.Queue()
        self._pool: asyncpg.Pool | None = None
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._database_url:
            self._pool = await asyncpg.create_pool(self._database_url, statement_cache_size=0)
            await self._ensure_schema()
            self._worker_task = asyncio.create_task(self._run_worker())

    async def _ensure_schema(self) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS run_id TEXT NULL;
                ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS agent_id TEXT NULL;
                ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS session_id TEXT NULL;

                CREATE INDEX IF NOT EXISTS idx_billing_line_items_run_id
                    ON billing_line_items(run_id) WHERE run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_billing_line_items_agent_id
                    ON billing_line_items(agent_id) WHERE agent_id IS NOT NULL;
                """
            )

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        if self._pool:
            await self._flush()
            await self._pool.close()

    def record_line_item(
        self,
        *,
        project_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_cents: Decimal,
        input_cost_cents: Decimal,
        output_cost_cents: Decimal,
        flat_fee_cost_cents: Decimal,
        currency: str = "USD",
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        ledger_event_id: str | None = None,
        end_user_id: str | None = None,
    ) -> str:
        """Enqueue line item for background flush. Returns event_id."""
        event_id = str(uuid.uuid4())
        event = BillingLineEvent(
            event_id=event_id,
            project_id=project_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_cents=cost_cents,
            input_cost_cents=input_cost_cents,
            output_cost_cents=output_cost_cents,
            flat_fee_cost_cents=flat_fee_cost_cents,
            currency=currency,
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            ledger_event_id=ledger_event_id,
            end_user_id=end_user_id,
        )
        self._queue.put_nowait(event)
        return event_id

    async def _run_worker(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        batch: list[BillingLineEvent] = []
        while not self._queue.empty() and len(batch) < self._max_batch_size:
            batch.append(self._queue.get_nowait())

        if not batch or not self._pool:
            return

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO billing_line_items (
                    id, project_id, ledger_event_id,
                    model, input_tokens, output_tokens, total_tokens,
                    sg_cost_cents, end_user_cost_cents,
                    input_cost_cents, output_cost_cents, flat_fee_cost_cents,
                    currency, run_id, agent_id, session_id, end_user_id
                )
                VALUES (
                    $1::uuid, $2, $3,
                    $4, $5, $6, $7,
                    $8, $8,
                    $9, $10, $11,
                    $12, $13, $14, $15, $16
                )
                ON CONFLICT (id) DO NOTHING
                """,
                [
                    (
                        e.event_id,
                        e.project_id,
                        e.ledger_event_id,
                        e.model,
                        e.input_tokens,
                        e.output_tokens,
                        e.total_tokens,
                        e.cost_cents,
                        e.input_cost_cents,
                        e.output_cost_cents,
                        e.flat_fee_cost_cents,
                        e.currency,
                        e.run_id,
                        e.agent_id,
                        e.session_id,
                        e.end_user_id,
                    )
                    for e in batch
                ],
            )
