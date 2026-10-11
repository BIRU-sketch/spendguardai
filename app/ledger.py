"""
Durable ledger — records token usage and cost per agent request into Postgres.

Batches writes in an asynchronous background worker off the critical request path.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from app.providers.base import Usage


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    project_id: str
    model: str
    usage: Usage
    run_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    cost_cents: Decimal = Decimal(0)
    end_user_id: str | None = None


class Ledger:
    """Accepts events instantly (non-blocking); a background task
    drains and batch-writes them to Postgres on an interval."""

    def __init__(self, database_url: str, flush_interval_seconds: float = 1.0) -> None:
        self._database_url = database_url
        self._flush_interval = flush_interval_seconds
        self._queue: asyncio.Queue[LedgerEvent] = asyncio.Queue()
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
                CREATE TABLE IF NOT EXISTS ledger_events (
                    event_id        UUID PRIMARY KEY,
                    project_id      TEXT NOT NULL,
                    model           TEXT NOT NULL,
                    input_tokens    INT NOT NULL,
                    output_tokens   INT NOT NULL,
                    total_tokens    INT NOT NULL,
                    run_id          TEXT NULL,
                    agent_id        TEXT NULL,
                    session_id      TEXT NULL,
                    cost_cents      NUMERIC(12, 6) DEFAULT 0,
                    end_user_id     TEXT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS run_id TEXT NULL;
                ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS agent_id TEXT NULL;
                ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS session_id TEXT NULL;
                ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS cost_cents NUMERIC(12, 6) DEFAULT 0;
                ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS end_user_id TEXT NULL;

                CREATE INDEX IF NOT EXISTS idx_ledger_events_run_id ON ledger_events(run_id) WHERE run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_ledger_events_agent_id ON ledger_events(agent_id) WHERE agent_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_ledger_events_project_id ON ledger_events(project_id);
                """
            )

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        if self._pool:
            await self._flush()
            await self._pool.close()

    def record(
        self,
        *,
        project_id: str,
        model: str,
        usage: Usage,
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        cost_cents: Decimal = Decimal(0),
        end_user_id: str | None = None,
    ) -> str:
        """Non-blocking — call from the request path. Returns the event_id immediately."""
        event = LedgerEvent(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            model=model,
            usage=usage,
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            cost_cents=cost_cents,
            end_user_id=end_user_id,
        )
        self._queue.put_nowait(event)
        return event.event_id

    async def _run_worker(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        batch: list[LedgerEvent] = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())

        if not batch or not self._pool:
            return

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ledger_events
                    (event_id, project_id, model, input_tokens, output_tokens, total_tokens,
                     run_id, agent_id, session_id, cost_cents, end_user_id)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (event_id) DO NOTHING
                """,
                [
                    (
                        e.event_id,
                        e.project_id,
                        e.model,
                        e.usage.input_tokens,
                        e.usage.output_tokens,
                        e.usage.total_tokens,
                        e.run_id,
                        e.agent_id,
                        e.session_id,
                        e.cost_cents,
                        e.end_user_id,
                    )
                    for e in batch
                ],
            )
