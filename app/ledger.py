"""
Durable ledger — records real token usage per request into Postgres.

Target design (per the plan) is Redis Streams feeding this worker, so
events survive a process crash between being accepted and being written.
This version uses an in-process asyncio.Queue as a simpler stand-in:
non-blocking on the request path, but a crash before the queue drains
still loses whatever hadn't been flushed. Swap the queue for a Redis
Stream consumer later if that gap matters more than it does today — the
batching/write logic here doesn't need to change either way.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import asyncpg

from app.providers.base import Usage


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    project_id: str
    end_user_id: str | None
    model: str
    usage: Usage


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
        self._pool = await asyncpg.create_pool(self._database_url)
        self._worker_task = asyncio.create_task(self._run_worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        if self._pool:
            await self._pool.close()

    def record(self, project_id: str, end_user_id: str | None, model: str, usage: Usage) -> str:
        """Non-blocking — call from the request path. Returns the
        event_id immediately; the actual DB write happens later,
        off the request's critical path."""
        event = LedgerEvent(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            end_user_id=end_user_id,
            model=model,
            usage=usage,
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

        if not batch:
            return

        assert self._pool is not None
        async with self._pool.acquire() as conn:
            # event_id is a real uuid generated at record()-time, so a
            # retried/duplicated flush never double-counts.
            await conn.executemany(
                """
                insert into ledger_events
                    (event_id, project_id, end_user_id, model,
                     input_tokens, output_tokens, total_tokens)
                values ($1, $2, $3, $4, $5, $6, $7)
                on conflict (event_id) do nothing
                """,
                [
                    (
                        e.event_id,
                        e.project_id,
                        e.end_user_id,
                        e.model,
                        e.usage.input_tokens,
                        e.usage.output_tokens,
                        e.usage.total_tokens,
                    )
                    for e in batch
                ],
            )