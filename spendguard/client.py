"""
SpendGuard Client — Sync and Async Python SDK for Agent Cost Control.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Iterator, AsyncIterator

import httpx

from spendguard.errors import (
    APIError,
    AuthenticationError,
    BudgetExceededError,
    ProviderError,
    RunAbortedError,
    RunawayLoopError,
    SpendGuardError,
)
from spendguard.run import Run, AsyncRun
from spendguard.types import ChatResponse, ModelInfo, RunStatus, TokenUsage
from spendguard.wrappers.openai import wrap_openai_client


class _ChatCompletionsProxy:
    """Provides client.chat.completions.create() compatibility with OpenAI SDK."""

    def __init__(self, client: SpendGuard) -> None:
        self._client = client

    def create(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.chat(*args, **kwargs)


class _AsyncChatCompletionsProxy:
    """Provides async client.chat.completions.create() compatibility with OpenAI SDK."""

    def __init__(self, client: AsyncSpendGuard) -> None:
        self._client = client

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return await self._client.chat(*args, **kwargs)


class SpendGuard:
    """Synchronous SpendGuard SDK client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("SPENDGUARD_API_KEY")
            or os.getenv("ADMIN_API_KEY")
            or os.getenv("GATEWAY_API_KEY")
            or ""
        )
        self.base_url = (
            base_url
            or os.getenv("SPENDGUARD_BASE_URL")
            or "http://localhost:8000"
        ).rstrip("/")
        self.timeout = timeout
        self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout)

        # OpenAI compatibility proxy
        self.chat_completions = _ChatCompletionsProxy(self)

    def __enter__(self) -> SpendGuard:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def completions(self) -> _ChatCompletionsProxy:
        return self.chat_completions

    # ------------------------------------------------------------------
    # Easy Chat Completion
    # ------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]] | str,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget: float | None = None,
        budget_cents: int | None = None,
        max_steps: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        provider: str | None = None,
        stream: bool = False,
        extra_headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> ChatResponse | Iterator[dict[str, Any]]:
        """Executes a chat completion with hard budget bounds and runaway protection.

        Args:
            model: Model name (e.g. 'gpt-4o', 'claude-3-5-sonnet', 'gemini-2.0-flash').
            messages: List of message turns or a simple prompt string.
            run_id: Agent run/trajectory identifier.
            agent_id: Agent identifier.
            session_id: User session identifier.
            budget: Run budget ceiling in USD dollars (e.g. 0.50 for $0.50).
            budget_cents: Run budget ceiling in integer cents (e.g. 50).
            max_steps: Runaway step iteration ceiling for this run.
            tools: OpenAI-format function tools.
            provider: Explicit provider override ('openai', 'anthropic', 'gemini', 'groq').
            stream: Whether to stream responses.
        """
        # Format messages if given as raw string
        formatted_messages = (
            [{"role": "user", "content": messages}]
            if isinstance(messages, str)
            else messages
        )

        # Compute budget cents
        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)

        headers = self._build_headers(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            budget_cents=b_cents,
            max_steps=max_steps,
            provider=provider,
            extra=extra_headers,
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "stream": stream,
            **extra,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if run_id:
            payload["run_id"] = run_id
        if agent_id:
            payload["agent_id"] = agent_id

        if stream:
            return self._stream_chat(payload, headers)

        resp = self._http.post("/chat/completions", headers=headers, json=payload)
        self._check_response_errors(resp)

        return self._parse_chat_response(resp)

    def _stream_chat(self, payload: dict[str, Any], headers: dict[str, str]) -> Iterator[dict[str, Any]]:
        with self._http.stream("POST", "/chat/completions", headers=headers, json=payload) as response:
            self._check_response_errors(response)
            for line in response.iter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    continue

    # ------------------------------------------------------------------
    # Agent Run Trajectory Context
    # ------------------------------------------------------------------

    def run(
        self,
        run_id: str | None = None,
        *,
        budget: float | None = None,
        budget_cents: int | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
        auto_complete: bool = True,
    ) -> Run:
        """Context manager creating a bounded autonomous agent trajectory."""
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        b_usd = budget
        if b_usd is None and budget_cents is not None:
            b_usd = budget_cents / 100.0

        return Run(
            client=self,
            run_id=rid,
            agent_id=agent_id,
            session_id=session_id,
            budget_usd=b_usd,
            max_steps=max_steps,
            metadata=metadata,
            auto_complete=auto_complete,
        )

    # ------------------------------------------------------------------
    # OpenAI Client Wrapper
    # ------------------------------------------------------------------

    def wrap(
        self,
        openai_client: Any,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget: float | None = None,
        max_steps: int | None = None,
        provider: str | None = None,
    ) -> Any:
        """Wraps standard openai.OpenAI client with SpendGuard boundaries."""
        return wrap_openai_client(
            openai_client,
            gateway_url=self.base_url,
            api_key=self.api_key,
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            budget_usd=budget,
            max_steps=max_steps,
            provider=provider,
        )

    # ------------------------------------------------------------------
    # Run Management Lifecycle
    # ------------------------------------------------------------------

    def init_run(
        self,
        run_id: str,
        *,
        budget_cents: int | None = None,
        budget: float | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunStatus:
        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)

        body = {
            "run_id": run_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "budget_cents": b_cents,
            "max_steps": max_steps,
            "metadata": metadata,
        }
        resp = self._http.post("/v1/runs", headers=self._auth_headers(), json=body)
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def get_run(self, run_id: str) -> RunStatus:
        resp = self._http.get(f"/runs/{run_id}", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def set_run_budget(self, run_id: str, *, budget_cents: int | None = None, budget: float | None = None) -> RunStatus:
        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)
        resp = self._http.post(f"/runs/{run_id}/budget", headers=self._auth_headers(), json={"budget_cents": b_cents})
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def complete_run(self, run_id: str) -> RunStatus:
        resp = self._http.post(f"/runs/{run_id}/complete", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def abort_run(self, run_id: str, reason: str = "manual_abort") -> RunStatus:
        resp = self._http.post(f"/runs/{run_id}/abort", headers=self._auth_headers(), json={"reason": reason})
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def reset_run(self, run_id: str) -> RunStatus:
        resp = self._http.post(f"/runs/{run_id}/reset", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    def list_runs(
        self,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunStatus]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if agent_id:
            params["agent_id"] = agent_id
        if status:
            params["status"] = status
        resp = self._http.get("/runs", headers=self._auth_headers(), params=params)
        self._check_response_errors(resp)
        return [self._parse_run_status(r) for r in resp.json().get("runs", [])]

    # ------------------------------------------------------------------
    # Models & Workspace Info
    # ------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        resp = self._http.get("/v1/models", headers=self._auth_headers())
        self._check_response_errors(resp)
        results: list[ModelInfo] = []
        for m in resp.json().get("data", []):
            pricing = m.get("pricing", {})
            results.append(ModelInfo(
                id=m["id"],
                owned_by=m.get("owned_by", "unknown"),
                input_cents_per_1k=pricing.get("input_cents_per_1k", 0.0),
                output_cents_per_1k=pricing.get("output_cents_per_1k", 0.0),
            ))
        return results

    def get_balance(self) -> dict[str, Any]:
        resp = self._http.get("/project", headers=self._auth_headers())
        self._check_response_errors(resp)
        data = resp.json()
        cents = data.get("balance_cents", 0)
        return {
            "balance_cents": cents,
            "balance_usd": cents / 100.0,
            "currency": data.get("currency", "USD"),
            "project_id": data.get("project", {}).get("project_id"),
        }

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_headers(
        self,
        *,
        run_id: str | None,
        agent_id: str | None,
        session_id: str | None,
        budget_cents: int | None,
        max_steps: int | None,
        provider: str | None,
        extra: dict[str, str] | None,
    ) -> dict[str, str]:
        h = self._auth_headers()
        if run_id:
            h["X-SpendGuard-Run-Id"] = run_id
        if agent_id:
            h["X-SpendGuard-Agent-Id"] = agent_id
        if session_id:
            h["X-SpendGuard-Session-Id"] = session_id
        if budget_cents is not None:
            h["X-SpendGuard-Run-Budget-Cents"] = str(budget_cents)
        if max_steps is not None:
            h["X-SpendGuard-Max-Steps"] = str(max_steps)
        if provider:
            h["X-SpendGuard-Provider"] = provider
        if extra:
            h.update(extra)
        return h

    def _check_response_errors(self, resp: httpx.Response) -> None:
        if resp.status_code in (200, 201):
            return

        status = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {"error": {"message": resp.text}}

        err_obj = body.get("error") or body.get("detail") or body
        if isinstance(err_obj, dict) and "error" in err_obj and isinstance(err_obj["error"], dict):
            nested = err_obj["error"]
            msg = nested.get("message") or err_obj.get("message")
            reason = nested.get("reason") or err_obj.get("reason", "")
            scope = nested.get("scope") or err_obj.get("scope", "")
            run_id = nested.get("run_id") or err_obj.get("run_id")
            step_count = nested.get("step_count", err_obj.get("step_count", 0))
            spent_cents = nested.get("run_spent_cents", err_obj.get("run_spent_cents", 0))
            budget_cents = nested.get("run_budget_cents", err_obj.get("run_budget_cents"))
        elif isinstance(err_obj, dict):
            msg = err_obj.get("message")
            reason = err_obj.get("reason", "")
            scope = err_obj.get("scope", "")
            run_id = err_obj.get("run_id")
            step_count = err_obj.get("step_count", 0)
            spent_cents = err_obj.get("run_spent_cents", 0)
            budget_cents = err_obj.get("run_budget_cents")
        else:
            msg = str(err_obj)
            reason = ""
            scope = ""
            run_id = None
            step_count = 0
            spent_cents = 0
            budget_cents = None

        if status == 401:
            raise AuthenticationError(msg or "Invalid API key", status_code=401, details=body)

        if status == 429:
            if scope in ("run_budget", "project_balance") or reason in ("run_budget_exhausted", "insufficient_project_balance", "run_completed"):
                raise BudgetExceededError(
                    message=msg or "Run budget exhausted",
                    scope=scope or "run_budget",
                    reason=reason or "run_budget_exhausted",
                    run_id=run_id,
                    spent_cents=spent_cents,
                    budget_cents=budget_cents,
                    details=body,
                )
            elif scope == "runaway_loop" or "runaway" in reason or "loop" in reason or "max_steps" in reason or "velocity" in reason:
                raise RunawayLoopError(
                    message=msg or "Runaway loop detected",
                    reason=reason,
                    run_id=run_id,
                    step_count=step_count,
                    details=body,
                )
            elif reason in ("run_aborted", "manual_abort"):
                raise RunAbortedError(
                    message=msg or "Run was aborted by operator",
                    run_id=run_id,
                    details=body,
                )
            raise BudgetExceededError(message=msg or "Rate or spend limit exceeded", details=body)

        if status == 502:
            raise ProviderError(msg or "Upstream LLM provider error", status_code=502, details=body)

        raise APIError(msg or f"Gateway error ({status})", status_code=status, details=body)

    def _parse_chat_response(self, resp: httpx.Response) -> ChatResponse:
        data = resp.json()
        choice = data.get("choices", [{}])[0] if data.get("choices") else {}
        msg = choice.get("message", {})

        h = resp.headers
        cost_cents = int(h.get("x-spendguard-request-cost-cents") or 0)
        run_spent = int(h["x-spendguard-run-spent-cents"]) if "x-spendguard-run-spent-cents" in h else None
        run_rem = int(h["x-spendguard-run-remaining-cents"]) if "x-spendguard-run-remaining-cents" in h else None
        step = int(h["x-spendguard-run-step"]) if "x-spendguard-run-step" in h else None

        return ChatResponse(
            content=msg.get("content"),
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason"),
            usage=TokenUsage.from_dict(data.get("usage")),
            cost_cents=cost_cents,
            run_id=h.get("x-spendguard-run-id"),
            agent_id=h.get("x-spendguard-agent-id"),
            run_step=step,
            run_spent_cents=run_spent,
            run_remaining_cents=run_rem,
            tool_calls=msg.get("tool_calls"),
            raw=data,
        )

    def _parse_run_status(self, d: dict[str, Any]) -> RunStatus:
        return RunStatus(
            run_id=d.get("run_id", ""),
            project_id=d.get("project_id", ""),
            agent_id=d.get("agent_id"),
            session_id=d.get("session_id"),
            budget_cents=d.get("budget_cents"),
            spent_cents=d.get("spent_cents", 0),
            remaining_cents=d.get("remaining_cents"),
            step_count=d.get("step_count", 0),
            is_aborted=d.get("is_aborted", False),
            status=d.get("status", "active"),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Asynchronous SpendGuard Client
# ---------------------------------------------------------------------------

class AsyncSpendGuard:
    """Asynchronous SpendGuard SDK client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("SPENDGUARD_API_KEY")
            or os.getenv("ADMIN_API_KEY")
            or os.getenv("GATEWAY_API_KEY")
            or ""
        )
        self.base_url = (
            base_url
            or os.getenv("SPENDGUARD_BASE_URL")
            or "http://localhost:8000"
        ).rstrip("/")
        self.timeout = timeout
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        self.chat_completions = _AsyncChatCompletionsProxy(self)

    async def __aenter__(self) -> AsyncSpendGuard:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def completions(self) -> _AsyncChatCompletionsProxy:
        return self.chat_completions

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]] | str,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        budget: float | None = None,
        budget_cents: int | None = None,
        max_steps: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        provider: str | None = None,
        stream: bool = False,
        extra_headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> ChatResponse | AsyncIterator[dict[str, Any]]:
        formatted_messages = (
            [{"role": "user", "content": messages}]
            if isinstance(messages, str)
            else messages
        )

        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)

        headers = self._build_headers(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            budget_cents=b_cents,
            max_steps=max_steps,
            provider=provider,
            extra=extra_headers,
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "stream": stream,
            **extra,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if run_id:
            payload["run_id"] = run_id
        if agent_id:
            payload["agent_id"] = agent_id

        if stream:
            return self._stream_chat(payload, headers)

        resp = await self._http.post("/chat/completions", headers=headers, json=payload)
        self._check_response_errors(resp)

        return self._parse_chat_response(resp)

    async def _stream_chat(self, payload: dict[str, Any], headers: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
        async with self._http.stream("POST", "/chat/completions", headers=headers, json=payload) as response:
            self._check_response_errors(response)
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    continue

    def run(
        self,
        run_id: str | None = None,
        *,
        budget: float | None = None,
        budget_cents: int | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
        auto_complete: bool = True,
    ) -> AsyncRun:
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        b_usd = budget
        if b_usd is None and budget_cents is not None:
            b_usd = budget_cents / 100.0

        return AsyncRun(
            client=self,
            run_id=rid,
            agent_id=agent_id,
            session_id=session_id,
            budget_usd=b_usd,
            max_steps=max_steps,
            metadata=metadata,
            auto_complete=auto_complete,
        )

    def wrap(self, openai_client: Any, **kwargs: Any) -> Any:
        return wrap_openai_client(
            openai_client,
            gateway_url=self.base_url,
            api_key=self.api_key,
            **kwargs,
        )

    async def init_run(
        self,
        run_id: str,
        *,
        budget_cents: int | None = None,
        budget: float | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        max_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunStatus:
        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)

        body = {
            "run_id": run_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "budget_cents": b_cents,
            "max_steps": max_steps,
            "metadata": metadata,
        }
        resp = await self._http.post("/v1/runs", headers=self._auth_headers(), json=body)
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def get_run(self, run_id: str) -> RunStatus:
        resp = await self._http.get(f"/runs/{run_id}", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def set_run_budget(self, run_id: str, *, budget_cents: int | None = None, budget: float | None = None) -> RunStatus:
        b_cents = budget_cents
        if b_cents is None and budget is not None:
            b_cents = int(budget * 100)
        resp = await self._http.post(f"/runs/{run_id}/budget", headers=self._auth_headers(), json={"budget_cents": b_cents})
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def complete_run(self, run_id: str) -> RunStatus:
        resp = await self._http.post(f"/runs/{run_id}/complete", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def abort_run(self, run_id: str, reason: str = "manual_abort") -> RunStatus:
        resp = await self._http.post(f"/runs/{run_id}/abort", headers=self._auth_headers(), json={"reason": reason})
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def reset_run(self, run_id: str) -> RunStatus:
        resp = await self._http.post(f"/runs/{run_id}/reset", headers=self._auth_headers())
        self._check_response_errors(resp)
        return self._parse_run_status(resp.json().get("run", {}))

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunStatus]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if agent_id:
            params["agent_id"] = agent_id
        if status:
            params["status"] = status
        resp = await self._http.get("/runs", headers=self._auth_headers(), params=params)
        self._check_response_errors(resp)
        return [self._parse_run_status(r) for r in resp.json().get("runs", [])]

    async def list_models(self) -> list[ModelInfo]:
        resp = await self._http.get("/v1/models", headers=self._auth_headers())
        self._check_response_errors(resp)
        results: list[ModelInfo] = []
        for m in resp.json().get("data", []):
            pricing = m.get("pricing", {})
            results.append(ModelInfo(
                id=m["id"],
                owned_by=m.get("owned_by", "unknown"),
                input_cents_per_1k=pricing.get("input_cents_per_1k", 0.0),
                output_cents_per_1k=pricing.get("output_cents_per_1k", 0.0),
            ))
        return results

    async def get_balance(self) -> dict[str, Any]:
        resp = await self._http.get("/project", headers=self._auth_headers())
        self._check_response_errors(resp)
        data = resp.json()
        cents = data.get("balance_cents", 0)
        return {
            "balance_cents": cents,
            "balance_usd": cents / 100.0,
            "currency": data.get("currency", "USD"),
            "project_id": data.get("project", {}).get("project_id"),
        }

    # Reused helpers
    _auth_headers = SpendGuard._auth_headers
    _build_headers = SpendGuard._build_headers
    _check_response_errors = SpendGuard._check_response_errors
    _parse_chat_response = SpendGuard._parse_chat_response
    _parse_run_status = SpendGuard._parse_run_status
