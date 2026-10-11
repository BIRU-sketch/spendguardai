"""
Agent Run Trajectory Managers (Sync & Async).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, AsyncIterator

from spendguard.types import ChatResponse, RunStatus

if TYPE_CHECKING:
    from spendguard.client import SpendGuard, AsyncSpendGuard


class Run:
    """Synchronous Agent Run Trajectory Context."""

    def __init__(
        self,
        client: SpendGuard,
        run_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget_usd: float | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
        auto_complete: bool = True,
    ) -> None:
        self._client = client
        self.run_id = run_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.budget_usd = budget_usd
        self.max_steps = max_steps
        self.metadata = metadata or {}
        self.auto_complete = auto_complete

        # Live metrics updated on each completion
        self.spent_usd: float = 0.0
        self.remaining_usd: float | None = budget_usd
        self.step: int = 0
        self.is_aborted: bool = False
        self.status: str = "active"

    def __enter__(self) -> Run:
        # Register/init run on gateway
        b_cents = int(self.budget_usd * 100) if self.budget_usd is not None else None
        status = self._client.init_run(
            run_id=self.run_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            budget_cents=b_cents,
            max_steps=self.max_steps,
            metadata=self.metadata,
        )
        self._apply_status(status)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None and self.auto_complete and self.status == "active":
            try:
                self.complete()
            except Exception:
                pass

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]] | str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        provider: str | None = None,
        stream: bool = False,
        **extra: Any,
    ) -> ChatResponse | Iterator[dict[str, Any]]:
        """Executes a chat completion bounded by this run's budget and constraints."""
        b_cents = int(self.budget_usd * 100) if self.budget_usd is not None else None
        resp = self._client.chat(
            model=model,
            messages=messages,
            run_id=self.run_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            budget_cents=b_cents,
            max_steps=self.max_steps,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
            **extra,
        )

        if stream:
            def _sync_stream_gen() -> Iterator[dict[str, Any]]:
                try:
                    for chunk in resp:
                        yield chunk
                finally:
                    try:
                        self.refresh()
                    except Exception:
                        pass
            return _sync_stream_gen()

        if isinstance(resp, ChatResponse):
            if resp.run_spent_usd is not None:
                self.spent_usd = resp.run_spent_usd
            if resp.run_remaining_usd is not None:
                self.remaining_usd = resp.run_remaining_usd
            if resp.run_step is not None:
                self.step = resp.run_step

        return resp

    def set_budget(self, budget_usd: float) -> RunStatus:
        """Dynamically increases or updates this run's budget ceiling in USD."""
        self.budget_usd = budget_usd
        status = self._client.set_run_budget(self.run_id, budget_cents=int(budget_usd * 100))
        self._apply_status(status)
        return status

    def complete(self) -> RunStatus:
        """Marks this agent trajectory as completed."""
        status = self._client.complete_run(self.run_id)
        self._apply_status(status)
        return status

    def abort(self, reason: str = "manual_abort") -> RunStatus:
        """Emergency circuit breaker: immediately stops this run."""
        status = self._client.abort_run(self.run_id, reason=reason)
        self._apply_status(status)
        return status

    def refresh(self) -> RunStatus:
        """Refreshes status and metrics from the gateway."""
        status = self._client.get_run(self.run_id)
        self._apply_status(status)
        return status

    def _apply_status(self, status: RunStatus) -> None:
        self.spent_usd = status.spent_usd
        self.remaining_usd = status.remaining_usd
        self.step = status.step_count
        self.is_aborted = status.is_aborted
        self.status = status.status


class AsyncRun:
    """Asynchronous Agent Run Trajectory Context."""

    def __init__(
        self,
        client: AsyncSpendGuard,
        run_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget_usd: float | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
        auto_complete: bool = True,
    ) -> None:
        self._client = client
        self.run_id = run_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.budget_usd = budget_usd
        self.max_steps = max_steps
        self.metadata = metadata or {}
        self.auto_complete = auto_complete

        self.spent_usd: float = 0.0
        self.remaining_usd: float | None = budget_usd
        self.step: int = 0
        self.is_aborted: bool = False
        self.status: str = "active"

    async def __aenter__(self) -> AsyncRun:
        b_cents = int(self.budget_usd * 100) if self.budget_usd is not None else None
        status = await self._client.init_run(
            run_id=self.run_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            budget_cents=b_cents,
            max_steps=self.max_steps,
            metadata=self.metadata,
        )
        self._apply_status(status)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None and self.auto_complete and self.status == "active":
            try:
                await self.complete()
            except Exception:
                pass

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]] | str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        provider: str | None = None,
        stream: bool = False,
        **extra: Any,
    ) -> ChatResponse | AsyncIterator[dict[str, Any]]:
        """Executes a chat completion bounded by this run's budget and constraints."""
        b_cents = int(self.budget_usd * 100) if self.budget_usd is not None else None
        resp = await self._client.chat(
            model=model,
            messages=messages,
            run_id=self.run_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            budget_cents=b_cents,
            max_steps=self.max_steps,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
            **extra,
        )

        if stream:
            async def _async_stream_gen() -> AsyncIterator[dict[str, Any]]:
                try:
                    async for chunk in resp:
                        yield chunk
                finally:
                    try:
                        await self.refresh()
                    except Exception:
                        pass
            return _async_stream_gen()

        if isinstance(resp, ChatResponse):
            if resp.run_spent_usd is not None:
                self.spent_usd = resp.run_spent_usd
            if resp.run_remaining_usd is not None:
                self.remaining_usd = resp.run_remaining_usd
            if resp.run_step is not None:
                self.step = resp.run_step

        return resp

    async def set_budget(self, budget_usd: float) -> RunStatus:
        self.budget_usd = budget_usd
        status = await self._client.set_run_budget(self.run_id, budget_cents=int(budget_usd * 100))
        self._apply_status(status)
        return status

    async def complete(self) -> RunStatus:
        status = await self._client.complete_run(self.run_id)
        self._apply_status(status)
        return status

    async def abort(self, reason: str = "manual_abort") -> RunStatus:
        status = await self._client.abort_run(self.run_id, reason=reason)
        self._apply_status(status)
        return status

    async def refresh(self) -> RunStatus:
        status = await self._client.get_run(self.run_id)
        self._apply_status(status)
        return status

    def _apply_status(self, status: RunStatus) -> None:
        self.spent_usd = status.spent_usd
        self.remaining_usd = status.remaining_usd
        self.step = status.step_count
        self.is_aborted = status.is_aborted
        self.status = status.status
