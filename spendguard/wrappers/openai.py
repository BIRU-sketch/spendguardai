"""
Wrapper for standard OpenAI & AsyncOpenAI client instances.
Injects SpendGuard run boundaries, spend limits, and handles runaway exceptions.
"""

from __future__ import annotations

import functools
from typing import Any

from spendguard.errors import (
    AuthenticationError,
    BudgetExceededError,
    RunawayLoopError,
    RunAbortedError,
    SpendGuardError,
)


def wrap_openai_client(
    client: Any,
    *,
    gateway_url: str,
    api_key: str,
    run_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    budget_usd: float | None = None,
    max_steps: int | None = None,
    provider: str | None = None,
) -> Any:
    """Wraps an existing openai.OpenAI or openai.AsyncOpenAI client instance."""
    # Point client base_url to SpendGuard gateway /v1
    clean_gateway = gateway_url.rstrip("/")
    if not clean_gateway.endswith("/v1"):
        clean_gateway += "/v1"

    # Set base_url on client
    try:
        client.base_url = clean_gateway
    except Exception:
        pass

    orig_create = client.chat.completions.create
    try:
        from openai import AsyncOpenAI
        is_async = isinstance(client, AsyncOpenAI)
    except Exception:
        is_async = "Async" in type(client).__name__ and not isinstance(client, MagicMock if "MagicMock" in globals() else type)

    default_headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
    }
    if run_id:
        default_headers["X-SpendGuard-Run-Id"] = run_id
    if agent_id:
        default_headers["X-SpendGuard-Agent-Id"] = agent_id
    if session_id:
        default_headers["X-SpendGuard-Session-Id"] = session_id
    if budget_usd is not None:
        default_headers["X-SpendGuard-Run-Budget-Cents"] = str(int(budget_usd * 100))
    if max_steps is not None:
        default_headers["X-SpendGuard-Max-Steps"] = str(max_steps)
    if provider:
        default_headers["X-SpendGuard-Provider"] = provider

    def _prepare_headers(extra_headers: dict[str, str] | None) -> dict[str, str]:
        headers = dict(default_headers)
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _handle_error(exc: Exception) -> None:
        # Check if exc has response status 429
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None) or {}
        if isinstance(body, dict):
            err_data = body.get("error", body.get("detail", {}))
            if isinstance(err_data, dict):
                reason = err_data.get("reason", "")
                scope = err_data.get("scope", "")
                if reason == "run_budget_exhausted" or scope == "run_budget":
                    raise BudgetExceededError(
                        message=err_data.get("message", "Run budget exhausted"),
                        scope="run_budget",
                        reason=reason,
                        run_id=err_data.get("run_id", run_id),
                        spent_cents=err_data.get("run_spent_cents", 0),
                        budget_cents=err_data.get("run_budget_cents"),
                        details=err_data,
                    ) from exc
                elif scope == "runaway_loop" or "runaway" in reason:
                    raise RunawayLoopError(
                        message=err_data.get("message", "Runaway loop detected"),
                        reason=reason,
                        run_id=err_data.get("run_id", run_id),
                        step_count=err_data.get("step_count", 0),
                        details=err_data,
                    ) from exc
                elif reason == "run_aborted":
                    raise RunAbortedError(
                        message=err_data.get("message", "Run was aborted"),
                        run_id=err_data.get("run_id", run_id),
                        details=err_data,
                    ) from exc

        if status == 401:
            raise AuthenticationError("SpendGuard API key invalid or unauthorized", status_code=401) from exc

    if is_async:
        @functools.wraps(orig_create)
        async def async_wrapped_create(*args: Any, **kwargs: Any) -> Any:
            kwargs["extra_headers"] = _prepare_headers(kwargs.get("extra_headers"))
            try:
                return await orig_create(*args, **kwargs)
            except Exception as exc:
                _handle_error(exc)
                raise exc

        client.chat.completions.create = async_wrapped_create
    else:
        @functools.wraps(orig_create)
        def sync_wrapped_create(*args: Any, **kwargs: Any) -> Any:
            kwargs["extra_headers"] = _prepare_headers(kwargs.get("extra_headers"))
            try:
                return orig_create(*args, **kwargs)
            except Exception as exc:
                _handle_error(exc)
                raise exc

        client.chat.completions.create = sync_wrapped_create

    return client
