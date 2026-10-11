"""
Runaway loop protection and circuit breaking for autonomous agent runs.

Detects and halts:
1. Step-count exhaustion: Agent exceeds max trajectory iterations.
2. High-frequency spinning: Agent enters an unthrottled while loop (> N calls in window).
3. Ping-pong / repeated loops: Repeated identical prompts or tool call inputs.
4. Tool call repetition: Consecutive repeated identical tool invocations with same arguments.
5. Manual kill-switch trips: Immediate operator abort signal.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis


class RunawayLoopDetected(Exception):
    """Raised when an agent run triggers runaway loop protection."""

    def __init__(
        self,
        message: str,
        reason: str,
        run_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.run_id = run_id
        self.details = details or {}


@dataclass(frozen=True)
class RunawayCheckResult:
    tripped: bool
    reason: str | None = None
    step_count: int = 0
    message: str | None = None


class RunawayDetector:
    """Redis-backed runaway loop detector and circuit breaker."""

    def __init__(
        self,
        redis_url: str,
        default_max_steps: int = 50,
        burst_window_seconds: int = 10,
        burst_max_requests: int = 15,
        repeat_history_depth: int = 4,
        max_identical_tool_calls: int = 3,
    ) -> None:
        self._redis_url = redis_url
        self.default_max_steps = default_max_steps
        self.burst_window_seconds = burst_window_seconds
        self.burst_max_requests = burst_max_requests
        self.repeat_history_depth = repeat_history_depth
        self.max_identical_tool_calls = max_identical_tool_calls
        self._redis: redis.Redis | None = None

    def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _run_aborted_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:aborted"

    def _run_step_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:steps"

    def _run_burst_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:burst"

    def _run_history_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:hash_history"

    def _run_tool_history_key(self, run_id: str) -> str:
        return f"sg:run:{run_id}:tool_history"

    async def check(
        self,
        *,
        run_id: str | None,
        messages: list[dict[str, Any]] | None = None,
        max_steps_override: int | None = None,
    ) -> RunawayCheckResult:
        """Evaluates runaway loop indicators before allowing upstream dispatch.
        If run_id is None, runaway checking per-run is skipped.
        """
        if not run_id:
            return RunawayCheckResult(tripped=False)

        r = self._get_redis()

        # 1. Check circuit breaker / manual abort flag
        abort_reason = await r.get(self._run_aborted_key(run_id))
        if abort_reason:
            return RunawayCheckResult(
                tripped=True,
                reason="run_aborted",
                message=f"Agent run {run_id} was halted by circuit breaker: {abort_reason}",
            )

        # 2. Check trajectory step iteration cap
        current_steps_str = await r.get(self._run_step_key(run_id))
        current_steps = int(current_steps_str or 0)
        max_steps = max_steps_override if max_steps_override is not None else self.default_max_steps

        if current_steps >= max_steps:
            return RunawayCheckResult(
                tripped=True,
                reason="max_steps_exceeded",
                step_count=current_steps,
                message=f"Agent run {run_id} reached trajectory step ceiling ({current_steps}/{max_steps})",
            )

        # 3. High-velocity spin detection (sliding window request counter)
        burst_key = self._run_burst_key(run_id)
        burst_count = await r.incr(burst_key)
        if burst_count == 1:
            await r.expire(burst_key, self.burst_window_seconds)

        if burst_count > self.burst_max_requests:
            return RunawayCheckResult(
                tripped=True,
                reason="velocity_threshold_exceeded",
                step_count=current_steps,
                message=(
                    f"Agent run {run_id} triggered runaway spin detection "
                    f"({burst_count} calls in {self.burst_window_seconds}s)"
                ),
            )

        # 4. Repeated identical prompt check (looping on identical prompt)
        if messages:
            try:
                # Hash the messages array
                serialized = json.dumps(messages, sort_keys=True)
                msg_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
                hist_key = self._run_history_key(run_id)

                await r.lpush(hist_key, msg_hash)
                await r.ltrim(hist_key, 0, self.repeat_history_depth - 1)
                await r.expire(hist_key, 3600)

                history = await r.lrange(hist_key, 0, self.repeat_history_depth - 1)
                if len(history) >= self.repeat_history_depth and all(h == msg_hash for h in history):
                    return RunawayCheckResult(
                        tripped=True,
                        reason="repeated_prompt_loop",
                        step_count=current_steps,
                        message=f"Agent run {run_id} stuck in repeated identical prompt loop",
                    )
            except Exception:
                pass

        # 5. Tool Call Repetition Check (detects stuck tool invocation loops)
        if messages and len(messages) > 0:
            last_msg = messages[-1]
            tool_calls = last_msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                try:
                    tool_sig = json.dumps([
                        {"name": tc.get("function", {}).get("name"), "args": tc.get("function", {}).get("arguments")}
                        for tc in tool_calls
                    ], sort_keys=True)
                    sig_hash = hashlib.sha256(tool_sig.encode("utf-8")).hexdigest()[:16]
                    t_hist_key = self._run_tool_history_key(run_id)

                    await r.lpush(t_hist_key, sig_hash)
                    await r.ltrim(t_hist_key, 0, self.max_identical_tool_calls - 1)
                    await r.expire(t_hist_key, 3600)

                    t_history = await r.lrange(t_hist_key, 0, self.max_identical_tool_calls - 1)
                    if len(t_history) >= self.max_identical_tool_calls and all(h == sig_hash for h in t_history):
                        return RunawayCheckResult(
                            tripped=True,
                            reason="tool_call_loop",
                            step_count=current_steps,
                            message=f"Agent run {run_id} stuck repeating identical tool call",
                        )
                except Exception:
                    pass

        return RunawayCheckResult(tripped=False, step_count=current_steps)

    async def record_step(self, run_id: str) -> int:
        """Increments the run's step count in Redis. Returns the new step count."""
        r = self._get_redis()
        step_key = self._run_step_key(run_id)
        new_step = await r.incr(step_key)
        await r.expire(step_key, 86400 * 7)  # 7-day retention
        return new_step

    async def abort_run(self, run_id: str, reason: str = "manual_abort") -> None:
        """Emergency circuit-breaker: immediately halts any future calls for this run_id."""
        r = self._get_redis()
        key = self._run_aborted_key(run_id)
        await r.set(key, reason, ex=86400 * 7)

    async def reset_run(self, run_id: str) -> None:
        """Resets all runaway tracking keys for a run_id."""
        r = self._get_redis()
        await r.delete(
            self._run_aborted_key(run_id),
            self._run_step_key(run_id),
            self._run_burst_key(run_id),
            self._run_history_key(run_id),
            self._run_tool_history_key(run_id),
        )

    async def get_metrics(self, run_id: str) -> dict[str, Any]:
        """Returns runaway loop status and counters for an agent run."""
        r = self._get_redis()
        steps = int(await r.get(self._run_step_key(run_id)) or 0)
        aborted_reason = await r.get(self._run_aborted_key(run_id))
        burst = int(await r.get(self._run_burst_key(run_id)) or 0)
        return {
            "run_id": run_id,
            "step_count": steps,
            "is_aborted": bool(aborted_reason),
            "abort_reason": aborted_reason,
            "burst_count": burst,
        }
