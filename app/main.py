"""
SpendGuard AI — Agent-Native Hard Cost Control Gateway.

Architectural Guarantees:
1. Agent & Run Identity: Native tracking of run_id, agent_id, session_id across headers & payloads.
2. Hard Spend Limits: Hard ceilings per run and per workspace, guaranteed atomic in Redis.
3. Pre-Flight Reservation + True-Up: Locks budget before model call, reconciles exact token cost on completion.
4. Runaway Loop Protection: Blocks iteration spirals, rapid looping, repeated prompts, and tool loops.
5. Multi-Tenant Workspace & Key Management: Multi-project scoping with SHA-256 keys.
6. Full SDK Compatibility: Direct drop-in for OpenAI, Anthropic, Gemini, LangChain, CrewAI, AutoGen.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv

from app.auth import (
    PostgresAPIKeyStore,
    Project,
    APIKeyInfo,
    extract_api_key,
    AuthError,
    generate_api_key,
)
from app.budget import (
    AgentBudgetManager,
    AgentRunContext,
    ReservationResult,
    RunStatus,
)
from app.runaway import (
    RunawayDetector,
    RunawayCheckResult,
)
from app.config import config
from app.ledger import Ledger
from app.billing import BillingWorker
from app.pricing import (
    TokenCost,
    compute_token_cost,
    sg_price_card_for,
    _SG_PRICE_CARDS,
)
from app.providers import (
    InternalRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    Usage,
    registry,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Singletons & Lifespan
# ---------------------------------------------------------------------------

_provider_registry = registry
_adapter = _provider_registry.get_adapter(model="default")
_key_store = PostgresAPIKeyStore(database_url=config.database_url)
_budget_manager = AgentBudgetManager(
    database_url=config.database_url,
    redis_url=config.redis_url,
)
_runaway_detector = RunawayDetector(
    redis_url=config.redis_url,
    default_max_steps=config.default_max_steps_per_run,
)
_ledger = Ledger(database_url=config.database_url)
_billing_worker = BillingWorker(database_url=config.database_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes persistent services and workers at gateway startup."""
    await asyncio.gather(
        _key_store.start(),
        _budget_manager.start(),
        _ledger.start(),
        _billing_worker.start(),
    )
    yield
    await asyncio.gather(
        _key_store.stop(),
        _budget_manager.stop(),
        _ledger.stop(),
        _billing_worker.stop(),
        return_exceptions=True,
    )


app = FastAPI(
    title="SpendGuard AI — Agent Cost Control Layer",
    description="Agent-native hard cost control layer with pre-flight reservation, true-up, and runaway protection.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Identity & Cost Estimation Helpers
# ---------------------------------------------------------------------------

def _to_int_cents(value: Decimal) -> int:
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _extract_run_context(
    body: dict[str, Any],
    *,
    h_run_id: str | None,
    h_agent_id: str | None,
    h_session_id: str | None,
    h_budget_cents: str | None,
    h_max_steps: str | None,
) -> AgentRunContext:
    """Extracts agent run identity and budget limits from headers or payload body."""
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}

    run_id = (
        (h_run_id and h_run_id.strip())
        or body.get("run_id")
        or meta.get("run_id")
        or body.get("trajectory_id")
        or None
    )
    agent_id = (
        (h_agent_id and h_agent_id.strip())
        or body.get("agent_id")
        or meta.get("agent_id")
        or None
    )
    session_id = (
        (h_session_id and h_session_id.strip())
        or body.get("session_id")
        or meta.get("session_id")
        or None
    )

    budget_cents: int | None = None
    if h_budget_cents:
        try:
            budget_cents = int(h_budget_cents)
        except ValueError:
            pass
    if budget_cents is None:
        raw_b = body.get("budget_cents") or body.get("run_budget_cents") or meta.get("budget_cents")
        if raw_b is not None:
            try:
                budget_cents = int(raw_b)
            except (ValueError, TypeError):
                pass

    max_steps: int | None = None
    if h_max_steps:
        try:
            max_steps = int(h_max_steps)
        except ValueError:
            pass
    if max_steps is None:
        raw_s = body.get("max_steps") or meta.get("max_steps")
        if raw_s is not None:
            try:
                max_steps = int(raw_s)
            except (ValueError, TypeError):
                pass

    return AgentRunContext(
        run_id=str(run_id) if run_id else None,
        agent_id=str(agent_id) if agent_id else None,
        session_id=str(session_id) if session_id else None,
        budget_cents=budget_cents,
        max_steps=max_steps,
        metadata=meta if meta else None,
    )


def _estimate_cost_cents(model: str, messages: list[dict[str, Any]], max_tokens: int | None) -> int:
    """Estimates pre-flight request cost in USD cents based on input length and max_tokens."""
    rough_input_chars = len(json.dumps(messages, ensure_ascii=False))
    rough_input_tokens = max(128, rough_input_chars // 4)
    est_output_tokens = max_tokens if max_tokens is not None else 512
    card = sg_price_card_for(model)
    tc = compute_token_cost(card, rough_input_tokens, est_output_tokens, currency="USD")
    est_cents = _to_int_cents(tc.total_cents)
    return max(1, est_cents)


def _build_internal_request(body: dict[str, Any], stream: bool = False) -> InternalRequest:
    known = {"model", "messages", "stream", "max_tokens", "temperature", "tools", "tool_choice", "top_p"}
    extra = {k: v for k, v in body.items() if k not in known}
    return InternalRequest(
        model=body["model"],
        messages=body.get("messages", []),
        stream=stream,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        tools=body.get("tools"),
        tool_choice=body.get("tool_choice"),
        top_p=body.get("top_p"),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Auth Dependency
# ---------------------------------------------------------------------------

async def get_current_project(request: Request) -> Project:
    """Authenticates inbound requests using Bearer API keys against Postgres/KeyStore."""
    auth_header = request.headers.get("Authorization")
    try:
        raw_key = extract_api_key(auth_header)
        return await _key_store.resolve(raw_key)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": str(exc), "type": "authentication_error"}},
        ) from exc


def _require_admin(request: Request) -> None:
    """Enforces admin authentication for cross-project / platform actions."""
    if not config.admin_api_key:
        return
    auth_header = request.headers.get("Authorization")
    raw_key = auth_header.replace("Bearer ", "").strip() if auth_header else ""
    if raw_key != config.admin_api_key:
        raise HTTPException(status_code=403, detail="Admin authorization required")


# ---------------------------------------------------------------------------
# Core Gateway: /chat/completions (OpenAI Compatible)
# ---------------------------------------------------------------------------

@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(
    body: dict[str, Any],
    request: Request,
    project: Project = Depends(get_current_project),
    x_run_id: str | None = Header(default=None, alias="X-SpendGuard-Run-Id"),
    x_run_id_alt: str | None = Header(default=None, alias="X-Run-Id"),
    x_agent_id: str | None = Header(default=None, alias="X-SpendGuard-Agent-Id"),
    x_agent_id_alt: str | None = Header(default=None, alias="X-Agent-Id"),
    x_session_id: str | None = Header(default=None, alias="X-SpendGuard-Session-Id"),
    x_session_id_alt: str | None = Header(default=None, alias="X-Session-Id"),
    x_budget_cents: str | None = Header(default=None, alias="X-SpendGuard-Run-Budget-Cents"),
    x_max_steps: str | None = Header(default=None, alias="X-SpendGuard-Max-Steps"),
    x_provider: str | None = Header(default=None, alias="X-SpendGuard-Provider"),
):
    """Agent-native completions proxy.

    Guarantees:
    - Pre-flight budget reservation across run and project
    - Runaway loop protection check
    - Dynamic LLM provider adapter dispatch (OpenAI, Anthropic, Gemini, Groq, OpenRouter)
    - True-up refund on completion
    - Ledger audit record
    """
    if "model" not in body:
        raise HTTPException(status_code=400, detail={"error": {"message": "Field 'model' is required", "type": "invalid_request_error"}})

    run_context = _extract_run_context(
        body,
        h_run_id=x_run_id or x_run_id_alt,
        h_agent_id=x_agent_id or x_agent_id_alt,
        h_session_id=x_session_id or x_session_id_alt,
        h_budget_cents=x_budget_cents,
        h_max_steps=x_max_steps,
    )
    messages = body.get("messages", [])

    # 1. Runaway loop protection check
    runaway_check = await _runaway_detector.check(
        run_id=run_context.run_id,
        messages=messages,
        max_steps_override=run_context.max_steps,
    )
    if runaway_check.tripped:
        err_msg = runaway_check.message or "Runaway loop detected"
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "message": err_msg,
                    "type": "runaway_loop_error",
                    "reason": runaway_check.reason,
                    "scope": "runaway_loop",
                    "run_id": run_context.run_id,
                    "step_count": runaway_check.step_count,
                },
                "reason": runaway_check.reason,
                "scope": "runaway_loop",
                "run_id": run_context.run_id,
                "step_count": runaway_check.step_count,
            },
        )

    # 2. Cost estimation
    est_cents = _estimate_cost_cents(body["model"], messages, body.get("max_tokens"))

    # 3. Resolve Provider Adapter (OpenAI, Anthropic, Gemini, Groq, OpenRouter)
    adapter = _provider_registry.get_adapter(model=body["model"], provider_hint=x_provider)

    # 4. Pre-flight atomic reservation
    res = await _budget_manager.reserve(
        project_id=project.project_id,
        run_context=run_context,
        estimated_cost_cents=est_cents,
    )
    if not res.allowed:
        err_msg = f"Spend limit exceeded for {res.scope}: {res.reason}"
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "message": err_msg,
                    "type": "budget_exceeded_error",
                    "reason": res.reason,
                    "scope": res.scope,
                    "run_id": res.run_id,
                    "run_spent_cents": res.run_spent_cents,
                    "run_budget_cents": res.run_budget_cents,
                    "project_remaining_cents": res.project_remaining_cents,
                },
                "reason": res.reason,
                "scope": res.scope,
                "run_id": res.run_id,
                "run_spent_cents": res.run_spent_cents,
                "run_budget_cents": res.run_budget_cents,
                "project_remaining_cents": res.project_remaining_cents,
            },
        )

    stream = bool(body.get("stream"))
    internal_req = _build_internal_request(body, stream=stream)
    provider_payload = adapter.translate_request(internal_req)

    # 5. Handle Streaming Response
    if stream:
        resp_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-SpendGuard-Project-Remaining-Cents": str(res.project_remaining_cents),
            "X-SpendGuard-Currency": "USD",
            "X-SpendGuard-Request-Cost-Cents": str(est_cents),
        }
        if res.run_id:
            resp_headers["X-SpendGuard-Run-Id"] = res.run_id
            resp_headers["X-SpendGuard-Run-Spent-Cents"] = str(res.run_spent_cents)
            if res.run_remaining_cents is not None:
                resp_headers["X-SpendGuard-Run-Remaining-Cents"] = str(res.run_remaining_cents)
        if run_context.agent_id:
            resp_headers["X-SpendGuard-Agent-Id"] = run_context.agent_id

        return StreamingResponse(
            _stream_agent_response(
                adapter=adapter,
                provider_payload=provider_payload,
                project=project,
                run_context=run_context,
                reservation_id=res.reservation_id,
                est_cents=est_cents,
            ),
            media_type="text/event-stream",
            headers=resp_headers,
        )

    # 6. Handle Non-Streaming Response
    try:
        response = await adapter.send(provider_payload)
    except ProviderError as exc:
        # Full refund of reserved cents on upstream failure
        await _budget_manager.refund_reservation(
            project_id=project.project_id,
            run_id=run_context.run_id,
            reservation_id=res.reservation_id,
            reserved_cents=est_cents,
        )
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail={"error": {"message": str(exc), "type": "upstream_provider_error"}},
        ) from exc

    usage = adapter.extract_usage(response)
    card = sg_price_card_for(body["model"])
    actual_tc = compute_token_cost(card, usage.input_tokens, usage.output_tokens)
    actual_cents = _to_int_cents(actual_tc.total_cents)

    # 7. Post-execution True-up & Step Recording
    await _budget_manager.true_up(
        project_id=project.project_id,
        run_id=run_context.run_id,
        reservation_id=res.reservation_id,
        estimated_cents=est_cents,
        actual_cents=actual_cents,
    )
    current_step = 0
    if run_context.run_id:
        current_step = await _runaway_detector.record_step(run_context.run_id)

    # 8. Durable Ledger & Billing Line Item (Async Non-Blocking)
    ledger_id = _ledger.record(
        project_id=project.project_id,
        model=body["model"],
        usage=usage,
        run_id=run_context.run_id,
        agent_id=run_context.agent_id,
        session_id=run_context.session_id,
        cost_cents=actual_tc.total_cents,
    )
    _billing_worker.record_line_item(
        project_id=project.project_id,
        model=body["model"],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cost_cents=actual_tc.total_cents,
        input_cost_cents=actual_tc.input_cost_cents,
        output_cost_cents=actual_tc.output_cost_cents,
        flat_fee_cost_cents=actual_tc.flat_fee_cents,
        currency="USD",
        run_id=run_context.run_id,
        agent_id=run_context.agent_id,
        session_id=run_context.session_id,
        ledger_event_id=ledger_id,
    )

    # 9. Enrich Response with Agent Cost Headers
    run_status: RunStatus | None = None
    if run_context.run_id:
        run_status = await _budget_manager.get_run_status(run_context.run_id, project.project_id)

    proj_bal = await _budget_manager.get_project_balance(project.project_id)
    resp_headers: dict[str, str] = {
        "X-SpendGuard-Project-Remaining-Cents": str(proj_bal),
        "X-SpendGuard-Currency": "USD",
        "X-SpendGuard-Request-Cost-Cents": str(actual_cents),
    }
    if run_status:
        resp_headers["X-SpendGuard-Run-Id"] = run_status.run_id
        resp_headers["X-SpendGuard-Run-Spent-Cents"] = str(run_status.spent_cents)
        if run_status.remaining_cents is not None:
            resp_headers["X-SpendGuard-Run-Remaining-Cents"] = str(run_status.remaining_cents)
        resp_headers["X-SpendGuard-Run-Step"] = str(current_step)
        if run_status.agent_id:
            resp_headers["X-SpendGuard-Agent-Id"] = run_status.agent_id

    return JSONResponse(
        status_code=response.status_code,
        content=response.payload,
        headers=resp_headers,
    )


async def _stream_agent_response(
    adapter: ProviderAdapter,
    provider_payload: dict[str, Any],
    project: Project,
    run_context: AgentRunContext,
    reservation_id: str | None,
    est_cents: int,
):
    """SSE generator streaming upstream tokens and performing post-stream true-up."""
    last_chunk: dict[str, Any] | None = None
    streamed_chars = 0
    settled = False
    try:
        async for chunk in adapter.stream(provider_payload):
            last_chunk = chunk
            choices = chunk.get("choices")
            if choices and isinstance(choices, list) and len(choices) > 0:
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    streamed_chars += len(content)
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
    except ProviderError as exc:
        settled = True
        await _budget_manager.refund_reservation(
            project_id=project.project_id,
            run_id=run_context.run_id,
            reservation_id=reservation_id,
            reserved_cents=est_cents,
        )
        yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_provider_error'}})}\n\n"
        return
    except (Exception, asyncio.CancelledError, GeneratorExit):
        if not settled:
            settled = True
            await _budget_manager.refund_reservation(
                project_id=project.project_id,
                run_id=run_context.run_id,
                reservation_id=reservation_id,
                reserved_cents=est_cents,
            )
        raise
    finally:
        if not settled:
            settled = True
            if last_chunk is not None:
                usage = adapter.extract_stream_usage(last_chunk)
                if usage.total_tokens == 0 and streamed_chars > 0:
                    fallback_out = max(1, streamed_chars // 4)
                    messages = provider_payload.get("messages", [])
                    fallback_in = max(32, len(json.dumps(messages)) // 4)
                    usage = Usage.from_input_output(
                        input_tokens=fallback_in,
                        output_tokens=fallback_out,
                    )

                model = provider_payload.get("model", "unknown")
                card = sg_price_card_for(model)
                actual_tc = compute_token_cost(card, usage.input_tokens, usage.output_tokens)
                actual_cents = _to_int_cents(actual_tc.total_cents)

                await _budget_manager.true_up(
                    project_id=project.project_id,
                    run_id=run_context.run_id,
                    reservation_id=reservation_id,
                    estimated_cents=est_cents,
                    actual_cents=actual_cents,
                )
                if run_context.run_id:
                    await _runaway_detector.record_step(run_context.run_id)

                ledger_id = _ledger.record(
                    project_id=project.project_id,
                    model=model,
                    usage=usage,
                    run_id=run_context.run_id,
                    agent_id=run_context.agent_id,
                    session_id=run_context.session_id,
                    cost_cents=actual_tc.total_cents,
                )
                _billing_worker.record_line_item(
                    project_id=project.project_id,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    cost_cents=actual_tc.total_cents,
                    input_cost_cents=actual_tc.input_cost_cents,
                    output_cost_cents=actual_tc.output_cost_cents,
                    flat_fee_cost_cents=actual_tc.flat_fee_cents,
                    currency="USD",
                    run_id=run_context.run_id,
                    agent_id=run_context.agent_id,
                    session_id=run_context.session_id,
                    ledger_event_id=ledger_id,
                )
            else:
                await _budget_manager.refund_reservation(
                    project_id=project.project_id,
                    run_id=run_context.run_id,
                    reservation_id=reservation_id,
                    reserved_cents=est_cents,
                )


# ---------------------------------------------------------------------------
# Models Endpoint (OpenAI Compatible)
# ---------------------------------------------------------------------------

@app.get("/models")
@app.get("/v1/models")
async def list_models(project: Project = Depends(get_current_project)):
    """Lists available models with current token price cards."""
    model_list = []
    for model_name, card in _SG_PRICE_CARDS.items():
        if model_name == "__default__":
            continue
        owner = "openai"
        if "claude" in model_name:
            owner = "anthropic"
        elif "gemini" in model_name:
            owner = "google"
        elif "llama" in model_name:
            owner = "meta"
        elif "deepseek" in model_name:
            owner = "deepseek"

        model_list.append({
            "id": model_name,
            "object": "model",
            "created": 1700000000,
            "owned_by": owner,
            "pricing": {
                "input_cents_per_1k": float(card.price_per_1k_input),
                "output_cents_per_1k": float(card.price_per_1k_output),
                "currency": "USD",
            },
        })
    return {"object": "list", "data": model_list}


# ---------------------------------------------------------------------------
# Agent Run Lifecycle Endpoints (SDK Primitives)
# ---------------------------------------------------------------------------

@app.post("/runs")
@app.post("/v1/runs")
async def init_run_endpoint(
    body: dict[str, Any],
    project: Project = Depends(get_current_project),
):
    """Starts or registers an agent run trajectory with spend and iteration caps."""
    run_id = body.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"
    agent_id = body.get("agent_id")
    session_id = body.get("session_id")
    budget_cents = body.get("budget_cents")
    max_steps = body.get("max_steps")
    metadata = body.get("metadata")

    b_cents = int(budget_cents) if budget_cents is not None else None
    m_steps = int(max_steps) if max_steps is not None else None

    run_status = await _budget_manager.init_run(
        project_id=project.project_id,
        run_id=run_id,
        agent_id=agent_id,
        session_id=session_id,
        budget_cents=b_cents,
        max_steps=m_steps,
        metadata=metadata,
    )
    return {"status": "ok", "run": run_status}


@app.get("/runs/{run_id}")
@app.get("/v1/runs/{run_id}")
async def get_run_status_endpoint(
    run_id: str,
    project: Project = Depends(get_current_project),
):
    """Inspect real-time spend, step iteration, budget limit, and status of an agent run."""
    status = await _budget_manager.get_run_status(run_id, project.project_id)
    return {"status": "ok", "run": status}


@app.post("/runs/{run_id}/budget")
@app.post("/v1/runs/{run_id}/budget")
async def set_run_budget_endpoint(
    run_id: str,
    body: dict[str, Any],
    project: Project = Depends(get_current_project),
):
    """Assigns or updates a hard spend ceiling (in cents) for a specific agent run."""
    budget_cents = body.get("budget_cents")
    if budget_cents is None or int(budget_cents) <= 0:
        raise HTTPException(status_code=400, detail={"error": "budget_cents must be a positive integer"})

    await _budget_manager.set_run_budget(run_id, project.project_id, int(budget_cents))
    status = await _budget_manager.get_run_status(run_id, project.project_id)
    return {"status": "ok", "run": status}


@app.post("/runs/{run_id}/complete")
@app.post("/v1/runs/{run_id}/complete")
async def complete_run_endpoint(
    run_id: str,
    project: Project = Depends(get_current_project),
):
    """Marks an agent run trajectory as completed."""
    status = await _budget_manager.complete_run(run_id, project.project_id)
    return {"status": "ok", "run": status}


@app.post("/runs/{run_id}/abort")
@app.post("/v1/runs/{run_id}/abort")
async def abort_run_endpoint(
    run_id: str,
    body: dict[str, Any] | None = None,
    project: Project = Depends(get_current_project),
):
    """Emergency circuit-breaker: immediately halts any subsequent calls for this run_id."""
    reason = (body or {}).get("reason", "manual_operator_abort")
    status = await _budget_manager.abort_run(run_id, project.project_id, reason=reason)
    return {"status": "ok", "run_id": run_id, "aborted": True, "reason": reason, "run": status}


@app.post("/runs/{run_id}/reset")
@app.post("/v1/runs/{run_id}/reset")
async def reset_run_endpoint(
    run_id: str,
    project: Project = Depends(get_current_project),
):
    """Resets runaway circuit breaker and step counters for a run."""
    await _runaway_detector.reset_run(run_id)
    status = await _budget_manager.get_run_status(run_id, project.project_id)
    return {"status": "ok", "run_id": run_id, "reset": True, "run": status}


@app.get("/runs")
@app.get("/v1/runs")
async def list_runs_endpoint(
    agent_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    project: Project = Depends(get_current_project),
):
    """Lists agent runs for the authenticated project workspace."""
    runs = await _budget_manager.list_runs(
        project_id=project.project_id,
        agent_id=agent_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"status": "ok", "runs": runs, "count": len(runs)}


@app.post("/admin/reaper")
async def trigger_reaper_endpoint(request: Request):
    """Emergency/admin trigger to sweep and recover abandoned reservations."""
    _require_admin(request)
    reaped = await _budget_manager.reap_abandoned_reservations()
    return {"status": "ok", "reaped_reservations": reaped}


# ---------------------------------------------------------------------------
# Project Workspace & Wallet Management
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "spendguard-cost-control", "version": "2.0.0"}


@app.get("/projects")
async def list_projects(request: Request, owner_id: str | None = None):
    _require_admin(request)
    projs = await _key_store.list_projects(owner_id=owner_id)
    return {"projects": projs}


@app.post("/projects")
async def create_project(body: dict[str, Any], request: Request):
    _require_admin(request)
    name = body.get("name", "Default Project")
    tier = body.get("tier", "free")
    owner_id = body.get("owner_id")
    initial_balance = body.get("initial_balance_cents", 1000)

    proj = await _key_store.create_project(name=name, tier=tier, owner_id=owner_id)
    await _budget_manager.topup_project_balance(proj.project_id, initial_balance)
    return {"status": "ok", "project": proj}


@app.get("/projects/{project_id}")
async def get_project(project_id: str, project: Project = Depends(get_current_project)):
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    proj = await _key_store.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    bal = await _budget_manager.get_project_balance(project_id)
    return {"project": proj, "balance_cents": bal, "currency": "USD"}


@app.post("/projects/{project_id}/topup")
async def topup_project(
    project_id: str,
    body: dict[str, Any],
    request: Request,
):
    _require_admin(request)
    cents = int(body.get("cents", 0))
    if cents <= 0:
        raise HTTPException(status_code=400, detail="cents must be positive")
    new_bal = await _budget_manager.topup_project_balance(project_id, cents)
    return {"status": "ok", "project_id": project_id, "balance_cents": new_bal}


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------

@app.post("/projects/{project_id}/keys")
async def create_project_key(
    project_id: str,
    body: dict[str, Any],
    request: Request,
):
    _require_admin(request)
    name = body.get("name", "Default Key")
    try:
        raw_key, key_info = await _key_store.create_key(project_id=project_id, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    return {
        "status": "ok",
        "api_key": key_info,
        "raw_key": raw_key,
        "warning": "Copy raw_key now; it cannot be retrieved again.",
    }


@app.get("/projects/{project_id}/keys")
async def list_project_keys(
    project_id: str,
    project: Project = Depends(get_current_project),
):
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    keys = await _key_store.list_keys(project_id=project_id)
    return {"keys": keys}


@app.delete("/projects/{project_id}/keys/{key_id}")
@app.post("/projects/{project_id}/keys/{key_id}/revoke")
async def revoke_project_key(
    project_id: str,
    key_id: str,
    project: Project = Depends(get_current_project),
):
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    success = await _key_store.revoke_key(project_id=project_id, key_id=key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok", "revoked": True}


# Current active project lookup
@app.get("/project")
async def get_active_project(project: Project = Depends(get_current_project)):
    bal = await _budget_manager.get_project_balance(project.project_id)
    return {"project": project, "balance_cents": bal, "currency": "USD"}


@app.get("/api-keys")
async def get_my_api_keys(project: Project = Depends(get_current_project)):
    keys = await _key_store.list_keys(project_id=project.project_id)
    return {"keys": keys}


@app.post("/api-keys")
async def create_my_api_key(
    body: dict[str, Any],
    project: Project = Depends(get_current_project),
):
    name = body.get("name", "API Key")
    try:
        raw_key, key_info = await _key_store.create_key(project_id=project.project_id, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    return {
        "status": "ok",
        "api_key": key_info,
        "raw_key": raw_key,
        "warning": "Copy raw_key now; it cannot be retrieved again.",
    }


@app.delete("/api-keys/{key_id}")
async def revoke_my_api_key(
    key_id: str,
    project: Project = Depends(get_current_project),
):
    success = await _key_store.revoke_key(project_id=project.project_id, key_id=key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok", "revoked": True}
