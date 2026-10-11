"""
Agent-native hard budget control, atomic pre-flight reservation, and true-up engine.

Hardened Distributed Guarantees:
1. 100% Atomic Lua Reservations: Zero-race condition budget and balance checks.
2. Leak-Proof Reservations with TTL & Reaper: If the server crashes mid-stream,
   abandoned reservations are automatically reaped and refunded via background worker.
3. Fail-Closed Run Caps: Concurrent requests never breach the assigned trajectory ceiling.
4. Exact True-Up: Immediate refund/adjustment upon completion or failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import redis.asyncio as redis

from app.config import config

logger = logging.getLogger("spendguard.budget")


# ---------------------------------------------------------------------------
# Lua Scripts for 100% Atomic Redis Operations
# ---------------------------------------------------------------------------

LUA_RESERVE = """
local project_bal_key = KEYS[1]
local run_spent_key = KEYS[2]
local run_budget_key = KEYS[3]
local run_status_key = KEYS[4]
local res_key = KEYS[5]
local active_zset = KEYS[6]

local est_cost = tonumber(ARGV[1])
local res_id = ARGV[2]
local res_json = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])
local expire_ts = tonumber(ARGV[5])
local has_run_id = tonumber(ARGV[6])
local def_proj_cents = tonumber(ARGV[7])

-- 1. Check run status if run_id present
if has_run_id == 1 then
    local status = redis.call("GET", run_status_key)
    if status == "completed" or status == "aborted" then
        return {"ERR", "run_" .. status, "run_status", "0", "0"}
    end

    -- Check hard run budget cap
    local budget_str = redis.call("GET", run_budget_key)
    if budget_str then
        local budget = tonumber(budget_str)
        local spent = tonumber(redis.call("GET", run_spent_key) or "0")
        if spent + est_cost > budget then
            return {"ERR", "run_budget_exhausted", "run_budget", tostring(spent), tostring(budget)}
        end
    end
end

-- 2. Check and initialize project balance
local proj_bal_str = redis.call("GET", project_bal_key)
local proj_bal = 0
if not proj_bal_str then
    redis.call("SET", project_bal_key, tostring(def_proj_cents))
    proj_bal = def_proj_cents
else
    proj_bal = tonumber(proj_bal_str)
end

if proj_bal < est_cost then
    return {"ERR", "insufficient_project_balance", "project_balance", "0", "0", tostring(proj_bal)}
end

-- 3. Atomic deductions & reservation storage
local new_proj_bal = redis.call("DECRBY", project_bal_key, est_cost)
local new_run_spent = 0
if has_run_id == 1 then
    new_run_spent = redis.call("INCRBY", run_spent_key, est_cost)
end

-- 4. Store reservation record with grace period TTL (2x) and register in active zset
redis.call("SET", res_key, res_json, "EX", ttl_seconds * 2)
redis.call("ZADD", active_zset, expire_ts, res_id)

return {"OK", tostring(new_proj_bal), tostring(new_run_spent)}
"""

LUA_TRUE_UP = """
local res_key = KEYS[1]
local active_zset = KEYS[2]
local proj_bal_key = KEYS[3]
local run_spent_key = KEYS[4]

local res_id = ARGV[1]
local actual_cents = tonumber(ARGV[2])
local has_run_id = tonumber(ARGV[3])

local res_str = redis.call("GET", res_key)
if not res_str then
    redis.call("ZREM", active_zset, res_id)
    return {"ALREADY_SETTLED"}
end

local cjson = cjson.decode(res_str)
local reserved_cents = tonumber(cjson["reserved_cents"])
local diff = reserved_cents - actual_cents

if diff ~= 0 then
    redis.call("INCRBY", proj_bal_key, diff)
    if has_run_id == 1 then
        redis.call("DECRBY", run_spent_key, diff)
    end
end

redis.call("DEL", res_key)
redis.call("ZREM", active_zset, res_id)

return {"OK", tostring(diff)}
"""

LUA_REFUND = """
local res_key = KEYS[1]
local active_zset = KEYS[2]
local proj_bal_key = KEYS[3]
local run_spent_key = KEYS[4]

local res_id = ARGV[1]
local has_run_id = tonumber(ARGV[2])

local res_str = redis.call("GET", res_key)
if not res_str then
    redis.call("ZREM", active_zset, res_id)
    return {"ALREADY_SETTLED"}
end

local cjson = cjson.decode(res_str)
local reserved_cents = tonumber(cjson["reserved_cents"])

redis.call("INCRBY", proj_bal_key, reserved_cents)
if has_run_id == 1 then
    redis.call("DECRBY", run_spent_key, reserved_cents)
end

redis.call("DEL", res_key)
redis.call("ZREM", active_zset, res_id)

return {"OK", tostring(reserved_cents)}
"""

LUA_REAP = """
local res_key = KEYS[1]
local active_zset = KEYS[2]
local proj_bal_key = KEYS[3]
local run_spent_key = KEYS[4]

local res_id = ARGV[1]

local res_str = redis.call("GET", res_key)
if not res_str then
    redis.call("ZREM", active_zset, res_id)
    return {"NOT_FOUND"}
end

local cjson = cjson.decode(res_str)
local reserved_cents = tonumber(cjson["reserved_cents"])
local run_id = cjson["run_id"]
local project_id = cjson["project_id"]

-- Full refund because this reservation timed out / was abandoned mid-stream
redis.call("INCRBY", proj_bal_key, reserved_cents)
if run_id and run_id ~= "" then
    redis.call("DECRBY", run_spent_key, reserved_cents)
end

redis.call("DEL", res_key)
redis.call("ZREM", active_zset, res_id)

return {"REAPED", tostring(reserved_cents), project_id, run_id or ""}
"""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentRunContext:
    """Identity and hard constraint parameters for an agent run or trajectory."""

    run_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    budget_cents: int | None = None      # Per-run hard spend ceiling in USD cents
    max_steps: int | None = None         # Per-run runaway iteration ceiling
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReservationResult:
    """Outcome of a pre-flight budget check and atomic reservation."""

    allowed: bool
    scope: str | None = None             # "run_budget" | "project_balance" | None
    reason: str | None = None
    run_id: str | None = None
    reservation_id: str | None = None    # Unique tracking ID for this reservation
    estimated_cents: int = 0
    run_spent_cents: int = 0
    run_budget_cents: int | None = None
    run_remaining_cents: int | None = None
    project_remaining_cents: int = 0


@dataclass(frozen=True)
class RunStatus:
    """Current state and spend metrics of an agent trajectory."""

    run_id: str
    project_id: str
    agent_id: str | None
    session_id: str | None
    budget_cents: int | None
    spent_cents: int
    remaining_cents: int | None
    step_count: int
    is_aborted: bool
    status: str                         # "active" | "budget_exceeded" | "aborted" | "completed"
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Core Budget Manager with Hardened Reservations & Reaper
# ---------------------------------------------------------------------------

class AgentBudgetManager:
    """Manages agent run budgets, multi-tenant project balances, and atomic reservations."""

    def __init__(
        self,
        database_url: str,
        redis_url: str,
        reservation_ttl_seconds: int | None = None,
        reaper_interval_seconds: float | None = None,
    ) -> None:
        self._database_url = database_url
        self._redis_url = redis_url
        self.reservation_ttl_seconds = reservation_ttl_seconds or config.reservation_ttl_seconds
        self.reaper_interval_seconds = reaper_interval_seconds or config.reaper_interval_seconds
        self._redis: redis.Redis | None = None
        self._pool: asyncpg.Pool | None = None
        self._reaper_task: asyncio.Task | None = None
        self._is_running = False

    async def start(self) -> None:
        self._is_running = True
        self._redis = redis.from_url(self._redis_url, decode_responses=True)
        if self._database_url:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                statement_cache_size=0,
                min_size=1,
                max_size=4,
            )
            await self._ensure_schema()

        # Start reaper background task
        self._reaper_task = asyncio.create_task(self._run_reaper())

    async def stop(self) -> None:
        self._is_running = False
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.aclose()
        if self._pool:
            await self._pool.close()

    def _require_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def _ensure_schema(self) -> None:
        """Creates lightweight agent_runs tracking table if it doesn't exist."""
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id              TEXT PRIMARY KEY,
                    project_id          TEXT NOT NULL,
                    agent_id            TEXT NULL,
                    session_id          TEXT NULL,
                    budget_cents        INT NULL,
                    spent_cents         INT NOT NULL DEFAULT 0,
                    step_count          INT NOT NULL DEFAULT 0,
                    status              TEXT NOT NULL DEFAULT 'active',
                    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id) WHERE agent_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id) WHERE session_id IS NOT NULL;
                """
            )

    # ------------------------------------------------------------------
    # Redis key naming conventions
    # ------------------------------------------------------------------

    def _run_spent_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:spent_cents"

    def _run_budget_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:budget_cents"

    def _run_meta_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:meta"

    def _run_status_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:status"

    def _project_balance_key(self, project_id: str) -> str:
        return f"project:{project_id}:balance_cents"

    def _reservation_key(self, reservation_id: str) -> str:
        return f"sg:res:{reservation_id}"

    @property
    def _active_reservations_zset(self) -> str:
        return "sg:reservations:active"

    # ------------------------------------------------------------------
    # Pre-Flight Atomic Reservation (Lua)
    # ------------------------------------------------------------------

    async def reserve(
        self,
        *,
        project_id: str,
        run_context: AgentRunContext,
        estimated_cost_cents: int,
        default_project_cents: int = 1000,
        ttl_seconds: int | None = None,
    ) -> ReservationResult:
        """100% atomic pre-flight check and reservation via Redis Lua script.

        Atomically guarantees:
        1. Run status is active.
        2. Run spend cap is not breached under high concurrency.
        3. Project balance is sufficient.
        4. Unique reservation record with TTL is stored for reaper recovery.
        """
        r = self._require_redis()
        run_id = run_context.run_id
        res_id = f"res_{uuid.uuid4().hex}"
        ttl = ttl_seconds or self.reservation_ttl_seconds
        expire_ts = time.time() + ttl

        # Prime budget & metadata if new
        run_budget: int | None = None
        if run_id:
            budget_val = await r.get(self._run_budget_key(run_id))
            if budget_val is not None:
                run_budget = int(budget_val)
            elif run_context.budget_cents is not None:
                run_budget = run_context.budget_cents
                await r.set(self._run_budget_key(run_id), run_budget, ex=86400 * 7)
            else:
                run_budget = config.default_run_budget_cents
                await r.setnx(self._run_budget_key(run_id), run_budget)

            if run_context.agent_id or run_context.session_id or run_context.metadata:
                meta = {
                    "project_id": project_id,
                    "agent_id": run_context.agent_id,
                    "session_id": run_context.session_id,
                    "metadata": run_context.metadata or {},
                }
                await r.setnx(self._run_meta_key(run_id), json.dumps(meta))
            await r.zadd(f"sg:project:{project_id}:runs", {run_id: time.time()})

        reservation_data = {
            "reservation_id": res_id,
            "project_id": project_id,
            "run_id": run_id or "",
            "reserved_cents": estimated_cost_cents,
            "created_at": time.time(),
            "expires_at": expire_ts,
        }

        keys = [
            self._project_balance_key(project_id),
            self._run_spent_key(run_id) if run_id else "sg:run:none:spent",
            self._run_budget_key(run_id) if run_id else "sg:run:none:budget",
            self._run_status_key(run_id) if run_id else "sg:run:none:status",
            self._reservation_key(res_id),
            self._active_reservations_zset,
        ]

        argv = [
            estimated_cost_cents,
            res_id,
            json.dumps(reservation_data),
            ttl,
            expire_ts,
            1 if run_id else 0,
            default_project_cents,
        ]

        result = await r.eval(LUA_RESERVE, len(keys), *keys, *argv)

        if result[0] == "ERR":
            reason = result[1]
            scope = result[2]
            spent = int(result[3]) if len(result) > 3 and result[3] else 0
            cap = int(result[4]) if len(result) > 4 and result[4] else run_budget
            rem = max(0, cap - spent) if cap is not None else None

            return ReservationResult(
                allowed=False,
                scope=scope,
                reason=reason,
                run_id=run_id,
                reservation_id=None,
                estimated_cents=estimated_cost_cents,
                run_spent_cents=spent,
                run_budget_cents=cap,
                run_remaining_cents=rem,
            )

        new_proj_bal = int(result[1])
        new_run_spent = int(result[2])
        run_rem = max(0, (run_budget or 0) - new_run_spent) if run_budget is not None else None

        return ReservationResult(
            allowed=True,
            run_id=run_id,
            reservation_id=res_id,
            estimated_cents=estimated_cost_cents,
            run_spent_cents=new_run_spent,
            run_budget_cents=run_budget,
            run_remaining_cents=run_rem,
            project_remaining_cents=new_proj_bal,
        )

    # ------------------------------------------------------------------
    # Post-Execution True-Up & Refund (Lua)
    # ------------------------------------------------------------------

    async def true_up(
        self,
        *,
        project_id: str,
        run_id: str | None,
        reservation_id: str | None = None,
        estimated_cents: int,
        actual_cents: int,
    ) -> None:
        """Atomic true-up releasing the reservation record and adjusting balances."""
        r = self._require_redis()

        if reservation_id:
            keys = [
                self._reservation_key(reservation_id),
                self._active_reservations_zset,
                self._project_balance_key(project_id),
                self._run_spent_key(run_id) if run_id else "sg:run:none:spent",
            ]
            argv = [
                reservation_id,
                actual_cents,
                1 if run_id else 0,
            ]
            await r.eval(LUA_TRUE_UP, len(keys), *keys, *argv)
        else:
            # Fallback if reservation_id was not provided
            diff = estimated_cents - actual_cents
            if diff != 0:
                await r.incrby(self._project_balance_key(project_id), diff)
                if run_id:
                    await r.decrby(self._run_spent_key(run_id), diff)

        # Best-effort async persistence of run spend to Postgres
        if run_id and self._pool:
            asyncio.create_task(self._persist_run_spend(project_id, run_id, actual_cents))

    async def refund_reservation(
        self,
        *,
        project_id: str,
        run_id: str | None,
        reservation_id: str | None = None,
        reserved_cents: int = 0,
    ) -> None:
        """Full refund of reservation on upstream failure or network abort."""
        r = self._require_redis()

        if reservation_id:
            keys = [
                self._reservation_key(reservation_id),
                self._active_reservations_zset,
                self._project_balance_key(project_id),
                self._run_spent_key(run_id) if run_id else "sg:run:none:spent",
            ]
            argv = [
                reservation_id,
                1 if run_id else 0,
            ]
            await r.eval(LUA_REFUND, len(keys), *keys, *argv)
        else:
            if reserved_cents <= 0:
                return
            await r.incrby(self._project_balance_key(project_id), reserved_cents)
            if run_id:
                await r.decrby(self._run_spent_key(run_id), reserved_cents)

    # ------------------------------------------------------------------
    # The Reaper: Recovers Leaked Reservations on Mid-Stream Death
    # ------------------------------------------------------------------

    async def reap_abandoned_reservations(self, max_batch: int = 50) -> int:
        """Sweeps and recovers reservations that exceeded their TTL without settling.

        Called automatically by the background reaper loop or on-demand.
        Returns the number of abandoned reservations reaped and refunded.
        """
        r = self._require_redis()
        now = time.time()

        # Find expired reservation IDs in sorted set
        expired_ids = await r.zrangebyscore(
            self._active_reservations_zset,
            min="-inf",
            max=now,
            start=0,
            num=max_batch,
        )

        if not expired_ids:
            return 0

        reaped_count = 0
        for res_id in expired_ids:
            res_key = self._reservation_key(res_id)
            # Read metadata first to get keys for Lua script
            res_str = await r.get(res_key)
            if not res_str:
                # Key already gone, clean zset
                await r.zrem(self._active_reservations_zset, res_id)
                continue

            try:
                res_data = json.loads(res_str)
                proj_id = res_data.get("project_id", "")
                r_id = res_data.get("run_id") or ""

                keys = [
                    res_key,
                    self._active_reservations_zset,
                    self._project_balance_key(proj_id),
                    self._run_spent_key(r_id) if r_id else "sg:run:none:spent",
                ]
                argv = [res_id]

                res = await r.eval(LUA_REAP, len(keys), *keys, *argv)
                if res and res[0] == "REAPED":
                    reaped_count += 1
                    logger.warning(
                        "[SpendGuard Reaper] Recovered abandoned reservation %s: refunded %s cents to project %s",
                        res_id,
                        res[1],
                        proj_id,
                    )
            except Exception as exc:
                logger.error("[SpendGuard Reaper] Error reaping reservation %s: %s", res_id, exc)

        return reaped_count

    async def _run_reaper(self) -> None:
        """Periodic background reaper loop."""
        while self._is_running:
            try:
                await asyncio.sleep(self.reaper_interval_seconds)
                await self.reap_abandoned_reservations()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[SpendGuard Reaper] Loop error: %s", exc)

    async def _persist_run_spend(self, project_id: str, run_id: str, actual_cents: int) -> None:
        """Background upsert of run spend into Postgres."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO agent_runs (run_id, project_id, spent_cents, step_count, updated_at)
                    VALUES ($1, $2, $3, 1, NOW())
                    ON CONFLICT (run_id) DO UPDATE SET
                        spent_cents = agent_runs.spent_cents + $3,
                        step_count = agent_runs.step_count + 1,
                        updated_at = NOW();
                    """,
                    run_id,
                    project_id,
                    actual_cents,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Agent Run Lifecycle (SDK Primitives)
    # ------------------------------------------------------------------

    async def init_run(
        self,
        *,
        project_id: str,
        run_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget_cents: int | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunStatus:
        """Explicitly starts/registers an agent run with budget and constraints."""
        r = self._require_redis()
        effective_budget = budget_cents if budget_cents is not None else config.default_run_budget_cents

        await r.set(self._run_budget_key(run_id), effective_budget, ex=86400 * 7)
        await r.set(self._run_status_key(run_id), "active", ex=86400 * 7)

        meta = {
            "project_id": project_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "max_steps": max_steps,
            "metadata": metadata or {},
        }
        await r.set(self._run_meta_key(run_id), json.dumps(meta), ex=86400 * 7)
        await r.zadd(f"sg:project:{project_id}:runs", {run_id: time.time()})

        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO agent_runs (run_id, project_id, agent_id, session_id, budget_cents, status, metadata, updated_at)
                        VALUES ($1, $2, $3, $4, $5, 'active', $6::jsonb, NOW())
                        ON CONFLICT (run_id) DO UPDATE SET
                            budget_cents = EXCLUDED.budget_cents,
                            agent_id = COALESCE(EXCLUDED.agent_id, agent_runs.agent_id),
                            session_id = COALESCE(EXCLUDED.session_id, agent_runs.session_id),
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW();
                        """,
                        run_id,
                        project_id,
                        agent_id,
                        session_id,
                        effective_budget,
                        json.dumps(metadata or {}),
                    )
            except Exception:
                pass

        return await self.get_run_status(run_id, project_id)

    async def set_run_budget(self, run_id: str, project_id: str, budget_cents: int) -> None:
        """Sets or updates the hard budget ceiling for an agent run."""
        if budget_cents <= 0:
            raise ValueError("budget_cents must be positive")
        r = self._require_redis()
        await r.set(self._run_budget_key(run_id), budget_cents, ex=86400 * 7)

        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO agent_runs (run_id, project_id, budget_cents, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (run_id) DO UPDATE SET
                            budget_cents = $3,
                            updated_at = NOW();
                        """,
                        run_id,
                        project_id,
                        budget_cents,
                    )
            except Exception:
                pass

    async def complete_run(self, run_id: str, project_id: str) -> RunStatus:
        """Marks an agent run trajectory as completed."""
        r = self._require_redis()
        await r.set(self._run_status_key(run_id), "completed", ex=86400 * 7)

        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE agent_runs SET status = 'completed', updated_at = NOW() WHERE run_id = $1",
                        run_id,
                    )
            except Exception:
                pass

        return await self.get_run_status(run_id, project_id)

    async def abort_run(self, run_id: str, project_id: str, reason: str = "manual_abort") -> RunStatus:
        """Emergency kill switch: marks run aborted and trips circuit breaker."""
        r = self._require_redis()
        await r.set(self._run_status_key(run_id), "aborted", ex=86400 * 7)
        await r.set(f"sg:run:{run_id}:aborted", reason, ex=86400 * 7)

        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE agent_runs SET status = 'aborted', updated_at = NOW() WHERE run_id = $1",
                        run_id,
                    )
            except Exception:
                pass

        return await self.get_run_status(run_id, project_id)

    async def get_run_status(self, run_id: str, project_id: str) -> RunStatus:
        """Retrieves real-time spend, budget, step count, and health of an agent run."""
        r = self._require_redis()
        spent = int(await r.get(self._run_spent_key(run_id)) or 0)
        budget_str = await r.get(self._run_budget_key(run_id))
        budget = int(budget_str) if budget_str is not None else None
        remaining = max(0, budget - spent) if budget is not None else None
        step_count = int(await r.get(f"sg:run:{run_id}:steps") or 0)
        aborted = bool(await r.get(f"sg:run:{run_id}:aborted"))
        status_override = await r.get(self._run_status_key(run_id))

        meta_str = await r.get(self._run_meta_key(run_id))
        meta = json.loads(meta_str) if meta_str else {}

        status = status_override or "active"
        if aborted:
            status = "aborted"
        elif budget is not None and spent >= budget and status == "active":
            status = "budget_exceeded"

        return RunStatus(
            run_id=run_id,
            project_id=project_id,
            agent_id=meta.get("agent_id"),
            session_id=meta.get("session_id"),
            budget_cents=budget,
            spent_cents=spent,
            remaining_cents=remaining,
            step_count=step_count,
            is_aborted=aborted,
            status=status,
            metadata=meta.get("metadata"),
        )

    async def list_runs(
        self,
        *,
        project_id: str,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunStatus]:
        """Lists agent runs for a project, optionally filtered by agent_id and status."""
        if not self._pool:
            r = self._require_redis()
            run_ids = await r.zrevrange(f"sg:project:{project_id}:runs", offset, offset + limit - 1)
            results: list[RunStatus] = []
            for rid in run_ids:
                st = await self.get_run_status(rid, project_id)
                if agent_id and st.agent_id != agent_id:
                    continue
                if status and st.status != status:
                    continue
                results.append(st)
            return results

        query = """
            SELECT run_id, project_id, agent_id, session_id, budget_cents, spent_cents, step_count, status, metadata
            FROM agent_runs
            WHERE project_id = $1
        """
        params: list[Any] = [project_id]

        if agent_id:
            params.append(agent_id)
            query += f" AND agent_id = ${len(params)}"

        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"

        params.append(limit)
        query += f" ORDER BY updated_at DESC LIMIT ${len(params)}"

        params.append(offset)
        query += f" OFFSET ${len(params)}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        results: list[RunStatus] = []
        for r in rows:
            b = r["budget_cents"]
            s = r["spent_cents"]
            rem = max(0, b - s) if b is not None else None
            meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {})
            results.append(RunStatus(
                run_id=r["run_id"],
                project_id=r["project_id"],
                agent_id=r["agent_id"],
                session_id=r["session_id"],
                budget_cents=b,
                spent_cents=s,
                remaining_cents=rem,
                step_count=r["step_count"],
                is_aborted=(r["status"] == "aborted"),
                status=r["status"],
                metadata=meta,
            ))
        return results

    # ------------------------------------------------------------------
    # Project Balance Management
    # ------------------------------------------------------------------

    async def get_project_balance(self, project_id: str, default_cents: int = 1000) -> int:
        r = self._require_redis()
        key = self._project_balance_key(project_id)
        val = await r.get(key)
        if val is None:
            await r.setnx(key, default_cents)
            return default_cents
        return int(val)

    async def topup_project_balance(self, project_id: str, cents: int) -> int:
        if cents <= 0:
            raise ValueError("Top-up amount must be positive")
        r = self._require_redis()
        new_balance = await r.incrby(self._project_balance_key(project_id), cents)
        return new_balance
