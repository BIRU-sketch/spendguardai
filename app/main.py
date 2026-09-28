"""
SpendGuard AI — Gateway + Microbilling entrypoint.

Request flow (hot path — UNCHANGED design from Stripe draft, just
Stripe→Chapa processor rename + currency/column renames):
  1. Auth (API key → Project)
  2. Extract end_user_id from header/body (optional → microbilling)
  3. Estimate token cost → TWO estimates (sg, end_user) in ETB cents
  4. Dual check_and_reserve:
        a. project-level RedisQuotaStore (existing)
        b. end_user-level f"user:{project}:{end_user_id}" Redis key — funded by grants
     429 if either fails, with scope field.
  5. Upstream call (stream or non-stream)
  6. True-up: refund (estimate − actual) back to BOTH Redis counters
  7. Non-blocking records: ledger, billing_line_item, consume_fifo() grants

Microbilling endpoints (PROCESSOR-AGNOSTIC shape; default processor = chapa):
  POST /billing/payout/onboarding    → create Chapa subaccount on founder's behalf (bank details)
  GET  /billing/payout/status        → current payout_account row
  GET  /billing/plans                → list plans (processor_price_id nullable for Chapa)
  POST /billing/plans                → create plan w/ charge+currency + HARD split validation
  PATCH /billing/plans/{plan_id}     → update plan (same hard validation)
  POST /billing/checkout             → Chapa /transaction/initialize → redirect
  POST /payment/webhook              → Chapa webhook: HMAC verify → grant_cents + INCRBY Redis
  GET  /end-users/{id}/balance       → grant-derived balance (Postgres truth + Redis cache)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from dotenv import load_dotenv

from app.auth import InMemoryAPIKeyStore, Project, extract_api_key, AuthError
from app.providers.base import InternalRequest, ProviderError, Usage
from app.providers.openai_compatible import OpenAICompatibleAdapter
from app.quota import RedisQuotaStore
from app.ledger import Ledger
from app.config import config
from app.pricing import (
    PriceCard,
    TokenCost,
    Money,
    compute_token_cost,
    sg_price_card_for,
)
from app.billing import (
    BillingWorker,
    PayoutAccountStore,
    EndUserStore,
    PaymentEventJournal,
)
from app.usage_grants import UsageGrantStore
from app.billing_plans import BillingPlanStore
from app.payment_processor import (
    build_processor,
    DatabasePlatformFeeSettings,
    PaymentProcessor,
    ProcessorError,
    ProcessorNotConfigured,
    PayoutAccountNotReady,
    PlatformFeeNotSet,
    WebhookVerificationFailed,
)

load_dotenv()

app = FastAPI(title="SpendGuard AI — gateway + microbilling (Chapa)")


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_adapter = OpenAICompatibleAdapter(
    base_url=config.upstream_base_url,
    api_key=config.upstream_base_key,
)
_key_store = InMemoryAPIKeyStore()
_quota_store = RedisQuotaStore(redis_url=config.redis_url)
_ledger = Ledger(database_url=config.database_url)
_billing_worker = BillingWorker(database_url=config.database_url)
_usage_grants = UsageGrantStore(database_url=config.database_url)
_payout_accounts = PayoutAccountStore(database_url=config.database_url)
_end_users = EndUserStore(database_url=config.database_url)
_payment_events = PaymentEventJournal(database_url=config.database_url)
_billing_plans = BillingPlanStore(database_url=config.database_url)


def _ledger_pool_getter():
    return _db_pool_read


_db_pool_read = None  # type: ignore  # populated in startup

# Processor singleton — None until config.chapa.secret_key is set.
_processor: PaymentProcessor | None = None


def _require_processor() -> PaymentProcessor:
    if _processor is None:
        raise ProcessorNotConfigured(
            "CHAPA_SECRET_KEY not set.  Configure it in .env to enable Chapa "
            "microbilling."
        )
    return _processor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_int_cents(value: Decimal) -> int:
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _end_user_redis_key(project_id: str, end_user_id: str) -> str:
    return f"user:{project_id}:{end_user_id}"


def _extract_end_user_id(
    body: Any,
    end_user_header: str | None,
) -> str | None:
    body_id = body.get("end_user_id") if isinstance(body, dict) else None
    if body_id:
        return str(body_id)
    if end_user_header:
        return end_user_header.strip() or None
    return None


# Default currency for SpendGuard-internal charges (what we charge the dev
# for core gateway access).  Independent of checkout-charge currency — the
# end-user plan currency is set per-plan.
_SG_DEFAULT_CURRENCY = "ETB"


async def _prime_end_user_redis_if_needed(
    project_id: str,
    end_user_id: str,
    *,
    currency: str = _SG_DEFAULT_CURRENCY,
) -> None:
    """First sighting: prime Redis from Postgres if the counter doesn't
    exist.  Currency-scoped."""
    import redis.asyncio as redis  # noqa: WPS433
    client = redis.from_url(config.redis_url, decode_responses=True)
    key = _end_user_redis_key(project_id, end_user_id)
    exists = await client.exists(key)
    if not exists:
        db_remaining = await _usage_grants.total_remaining_cents(
            project_id, end_user_id, currency=currency,
        )
        await client.setnx(key, db_remaining)
    await client.close()


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global _db_pool_read, _processor
    import asyncpg
    _db_pool_read = await asyncpg.create_pool(config.database_url, min_size=1, max_size=2)

    await _ledger.start()
    await _billing_worker.start()
    await _usage_grants.start()
    await _payout_accounts.start()
    await _end_users.start()
    await _payment_events.start()
    await _billing_plans.start()

    await _quota_store.set_balance("local-dev", 1000000)  # seed

    if config.chapa.secret_key:
        settings = DatabasePlatformFeeSettings(lambda: _db_pool_read)  # type: ignore
        _processor = build_processor(settings=settings)


@app.on_event("shutdown")
async def shutdown():
    await _ledger.stop()
    await _billing_worker.stop()
    await _usage_grants.stop()
    await _payout_accounts.stop()
    await _end_users.stop()
    await _payment_events.stop()
    await _billing_plans.stop()
    if _db_pool_read is not None:
        await _db_pool_read.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def get_current_project(authorization: str | None = Header(default=None)) -> Project:
    try:
        api_key = extract_api_key(authorization)
        return await _key_store.resolve(api_key)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _build_internal_request(body: dict, stream: bool) -> InternalRequest:
    return InternalRequest(
        model=body["model"],
        messages=body["messages"],
        stream=stream,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        extra={
            k: v
            for k, v in body.items()
            if k not in {"model", "messages", "stream", "max_tokens",
                         "temperature", "end_user_id", "end_user_metadata"}
        },
    )


# ---------------------------------------------------------------------------
# Core hot path
# ---------------------------------------------------------------------------

def _estimate_costs(
    model: str,
    max_tokens: int | None,
    input_tokens_estimate: int,
) -> tuple[TokenCost, PriceCard]:
    card = sg_price_card_for(model)
    out_est = max_tokens if max_tokens and max_tokens > 0 else 4096
    in_est = max(input_tokens_estimate, 128)
    return (
        compute_token_cost(card, in_est, out_est, currency=_SG_DEFAULT_CURRENCY),
        card,
    )


def _actual_costs(
    model: str,
    usage: Usage,
) -> tuple[TokenCost, PriceCard]:
    card = sg_price_card_for(model)
    return (
        compute_token_cost(
            card,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            currency=_SG_DEFAULT_CURRENCY,
        ),
        card,
    )


async def _dual_check_and_reserve(
    project_id: str,
    end_user_id: str | None,
    project_cents: int,
    end_user_cents: int,
) -> tuple[bool, str | None]:
    project_ok = True
    user_ok = True
    reason_scope = None

    qr = await _quota_store.check_and_reserve(project_id, project_cents)
    if not qr.allowed:
        project_ok = False
        reason_scope = "project"

    if end_user_id and project_ok:
        key = _end_user_redis_key(project_id, end_user_id)
        import redis.asyncio as redis
        r = redis.from_url(config.redis_url, decode_responses=True)
        current = await r.get(key)
        if current is None:
            remaining = await _usage_grants.total_remaining_cents(
                project_id, end_user_id, currency=_SG_DEFAULT_CURRENCY,
            )
            await r.setnx(key, remaining)
            current = str(remaining)
        current_cents = int(current or 0)
        if current_cents < end_user_cents:
            user_ok = False
            reason_scope = "end_user"
        else:
            await r.decrby(key, end_user_cents)
        await r.close()

    if not user_ok and project_ok:
        await _quota_store.refund(project_id, project_cents)

    return (project_ok and user_ok), reason_scope


async def _dual_refund(
    project_id: str,
    end_user_id: str | None,
    project_refund_cents: int,
    user_refund_cents: int,
) -> None:
    if project_refund_cents > 0:
        await _quota_store.refund(project_id, project_refund_cents)
    if end_user_id and user_refund_cents > 0:
        import redis.asyncio as redis
        key = _end_user_redis_key(project_id, end_user_id)
        r = redis.from_url(config.redis_url, decode_responses=True)
        await r.incrby(key, user_refund_cents)
        await r.close()


@app.post("/chat/completions")
async def chat_completions(
    body: dict,
    request: Request,
    project: Project = Depends(get_current_project),
    x_end_user_id: str | None = Header(default=None, alias="X-End-User-Id"),
    x_end_user_metadata: str | None = Header(default=None, alias="X-End-User-Metadata"),
):
    end_user_id = _extract_end_user_id(body, x_end_user_id)
    end_user_metadata: dict[str, Any] = {}
    if x_end_user_metadata:
        try:
            end_user_metadata = json.loads(x_end_user_metadata)
        except Exception:
            end_user_metadata = {"raw": x_end_user_metadata}
    body_metadata = body.get("end_user_metadata") if isinstance(body, dict) else None
    if isinstance(body_metadata, dict):
        end_user_metadata = {**end_user_metadata, **body_metadata}

    max_tokens = body.get("max_tokens")
    rough_input_chars = len(json.dumps(body.get("messages", []), ensure_ascii=False))
    rough_input_tokens_est = max(128, rough_input_chars // 4)
    est_tc, _ = _estimate_costs(body["model"], max_tokens, rough_input_tokens_est)
    est_sg_cents = _to_int_cents(est_tc.total_cents)
    if est_sg_cents <= 0:
        est_sg_cents = 1
    est_user_cents = est_sg_cents

    t0 = time.perf_counter()
    allowed, reason_scope = await _dual_check_and_reserve(
        project.project_id,
        end_user_id,
        project_cents=est_sg_cents,
        end_user_cents=est_user_cents,
    )
    t1 = time.perf_counter()
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": "Quota exceeded", "scope": reason_scope or "project"},
        )

    if end_user_id:
        import asyncio
        asyncio.create_task(
            _end_users.touch(project.project_id, end_user_id, end_user_metadata)
        )

    stream = bool(body.get("stream"))
    internal_req = _build_internal_request(body, stream=stream)
    provider_payload = _adapter.translate_request(internal_req)

    if stream:
        return StreamingResponse(
            _stream_response(
                provider_payload,
                project,
                end_user_id=end_user_id,
                est_sg_cents=est_sg_cents,
                est_user_cents=est_user_cents,
            ),
            media_type="text/event-stream",
        )

    try:
        response = await _adapter.send(provider_payload)
    except ProviderError as exc:
        await _dual_refund(project.project_id, end_user_id, est_sg_cents, est_user_cents)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    usage = _adapter.extract_usage(response)
    actual_tc, _ = _actual_costs(body["model"], usage)
    actual_sg_cents = _to_int_cents(actual_tc.total_cents)
    actual_user_cents = actual_sg_cents
    sg_project_refund = max(est_sg_cents - actual_sg_cents, 0)
    user_refund = max(est_user_cents - actual_user_cents, 0)
    await _dual_refund(project.project_id, end_user_id, sg_project_refund, user_refund)

    ledger_event_id = _ledger.record(
        project.project_id,
        end_user_id=end_user_id,
        model=body["model"],
        usage=usage,
    )

    bli_id = _billing_worker.record_line_item(
        project_id=project.project_id,
        end_user_id=end_user_id,
        ledger_event_id=ledger_event_id,
        model=body["model"],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        sg_cost_cents=Decimal(actual_sg_cents),
        end_user_cost_cents=Decimal(actual_user_cents),
        input_cost_cents=actual_tc.input_cost_cents,
        output_cost_cents=actual_tc.output_cost_cents,
        flat_fee_cost_cents=actual_tc.flat_fee_cents,
        currency=actual_tc.currency,
    )

    if end_user_id and actual_user_cents > 0:
        import asyncio
        async def _consume_grants_later():
            try:
                await _usage_grants.consume_fifo(
                    project_id=project.project_id,
                    end_user_id=end_user_id,
                    requested_cents=actual_user_cents,
                    currency=actual_tc.currency,
                    billing_line_item_id=bli_id,
                )
            except Exception:
                pass
        asyncio.create_task(_consume_grants_later())

    resp_headers: dict[str, str] = {
        "X-SG-Project-Remaining-Cents": str(
            await _safe_get_project_balance(project.project_id)
        ),
        "X-SG-Currency": _SG_DEFAULT_CURRENCY,
    }
    if end_user_id:
        resp_headers["X-SG-EndUser-Remaining-Cents"] = str(
            await _safe_get_user_balance(project.project_id, end_user_id)
        )
        resp_headers["X-SG-EndUser-Cost-Cents"] = str(actual_user_cents)
    return JSONResponse(
        status_code=response.status_code,
        content=response.payload,
        headers=resp_headers,
    )


async def _stream_response(
    provider_payload: Any,
    project: Project,
    *,
    end_user_id: str | None,
    est_sg_cents: int,
    est_user_cents: int,
):
    last_chunk: dict | None = None
    try:
        async for chunk in _adapter.stream(provider_payload):
            last_chunk = chunk
            yield f"data: {json.dumps(chunk)}\n\n"
    except ProviderError as exc:
        await _dual_refund(project.project_id, end_user_id, est_sg_cents, est_user_cents)
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        return

    if last_chunk is not None:
        usage = _adapter.extract_stream_usage(last_chunk)
        model = provider_payload.get("model", "unknown")
        actual_tc, _ = _actual_costs(model, usage)
        actual_sg_cents = _to_int_cents(actual_tc.total_cents)
        actual_user_cents = actual_sg_cents
        sg_refund = max(est_sg_cents - actual_sg_cents, 0)
        user_refund = max(est_user_cents - actual_user_cents, 0)
        await _dual_refund(project.project_id, end_user_id, sg_refund, user_refund)

        ledger_event_id = _ledger.record(
            project.project_id, end_user_id=end_user_id, model=model, usage=usage,
        )
        bli_id = _billing_worker.record_line_item(
            project_id=project.project_id,
            end_user_id=end_user_id,
            ledger_event_id=ledger_event_id,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            sg_cost_cents=Decimal(actual_sg_cents),
            end_user_cost_cents=Decimal(actual_user_cents),
            input_cost_cents=actual_tc.input_cost_cents,
            output_cost_cents=actual_tc.output_cost_cents,
            flat_fee_cost_cents=actual_tc.flat_fee_cents,
            currency=actual_tc.currency,
        )
        if end_user_id and actual_user_cents > 0:
            import asyncio
            async def _consume_grants_later():
                try:
                    await _usage_grants.consume_fifo(
                        project_id=project.project_id,
                        end_user_id=end_user_id,
                        requested_cents=actual_user_cents,
                        currency=actual_tc.currency,
                        billing_line_item_id=bli_id,
                    )
                except Exception:
                    pass
            asyncio.create_task(_consume_grants_later())

    yield "data: [DONE]\n\n"


async def _safe_get_project_balance(project_id: str) -> int:
    try:
        import redis.asyncio as redis
        r = redis.from_url(config.redis_url, decode_responses=True)
        val = await r.get(f"project:{project_id}:balance_cents")
        await r.close()
        return int(val or 0)
    except Exception:
        return -1


async def _safe_get_user_balance(project_id: str, end_user_id: str) -> int:
    try:
        import redis.asyncio as redis
        r = redis.from_url(config.redis_url, decode_responses=True)
        val = await r.get(_end_user_redis_key(project_id, end_user_id))
        await r.close()
        return int(val or 0)
    except Exception:
        return -1


# ===================================================================
# Microbilling endpoints — Payout onboarding, Plans, Checkout, Webhooks
# ===================================================================


# -------------------------------------------------------------------
# Payout onboarding (was: /billing/connect/onboarding)
#   - Founder submits bank details to SpendGuard
#   - SpendGuard calls Chapa POST /subaccount on their behalf
#   - Writes payout_accounts row with returned subaccount id
# -------------------------------------------------------------------

@app.post("/billing/payout/onboarding")
async def payout_onboarding(
    body: dict,
    project: Project = Depends(get_current_project),
):
    """Create Chapa subaccount for the caller's project.

    Body (SpendGuard founder submits their bank info to *SpendGuard*, not
    to Chapa directly — SpendGuard holds one Chapa platform key):
      - business_name       (e.g. project.display_name)
      - bank_code           (Chapa bank code, e.g. '801' for CBE)
      - account_number      (bank account number)
      - account_name        (name on the bank account)
      - split_type          'percentage' (founder gets charge*split_value)
                            or 'flat' (founder gets split_value ETB cents)
      - split_value         number — e.g. 0.30 (30%) or 500 (ETB 5.00 flat)
      - email               (optional — forwarded to Chapa)
    """
    proc = _require_processor()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    required = {"business_name", "bank_code", "account_number", "account_name",
                "split_type", "split_value"}
    missing = required - body.keys()
    if missing:
        raise HTTPException(status_code=400, detail={"missing": sorted(missing)})
    split_type = str(body["split_type"])
    split_value_raw = body["split_value"]
    try:
        split_value = Decimal(str(split_value_raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"split_value": "not a number"}) from exc

    try:
        result = await proc.create_payout_account(
            project_id=project.project_id,
            business_name=str(body["business_name"]),
            bank_code=str(body["bank_code"]),
            account_number=str(body["account_number"]),
            account_name=str(body["account_name"]),
            split_type=split_type,
            split_value=split_value,
            email=body.get("email") if isinstance(body.get("email"), str) else None,
        )
    except ProcessorError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    account = await _payout_accounts.upsert(
        project_id=project.project_id,
        processor=result.processor,
        processor_account_id=result.processor_account_id,
        bank_code=str(body["bank_code"]),
        account_number=str(body["account_number"]),
        account_name=str(body["account_name"]),
        split_type=split_type,
        split_value=split_value,
        onboarding_state=result.onboarding_state,
    )
    return {
        "project_id": account.project_id,
        "processor": account.processor,
        "processor_account_id": account.processor_account_id,
        "onboarding_state": account.onboarding_state,
        "split_type": account.split_type,
        "split_value": str(account.split_value),
        "note": (
            "Chapa subaccount created.  Mark onboarding_state='complete' via "
            "the operator dashboard once Chapa KYC is confirmed."
        ),
    }


@app.get("/billing/payout/status")
async def payout_status(
    project: Project = Depends(get_current_project),
):
    acc = await _payout_accounts.get(project.project_id)
    if acc is None:
        return {"status": "none", "next": "POST /billing/payout/onboarding"}
    return {
        "project_id": acc.project_id,
        "processor": acc.processor,
        "processor_account_id": acc.processor_account_id,
        "onboarding_state": acc.onboarding_state,
        "split_type": acc.split_type,
        "split_value": str(acc.split_value),
        "created_at": acc.created_at.isoformat(),
    }


# -------------------------------------------------------------------
# Billing plans — create/update/list with HARD validation
# -------------------------------------------------------------------

@app.get("/billing/plans")
async def list_plans(
    project: Project = Depends(get_current_project),
    include_archived: bool = False,
):
    plans = await _billing_plans.list(project.project_id, include_archived=include_archived)
    return {
        "plans": [
            {
                "project_id": p.project_id,
                "plan_id": p.plan_id,
                "display_name": p.display_name,
                "description": p.description,
                "processor_price_id": p.processor_price_id,
                "charge_amount_cents": p.charge_amount_cents,
                "currency": p.currency,
                "usage_allocation_cents": p.usage_allocation_cents,
                "expiry_days": p.expiry_days,
                "is_active": p.is_active,
                "sort_order": p.sort_order,
            }
            for p in plans
        ],
    }


@app.post("/billing/plans")
async def create_plan(
    body: dict,
    project: Project = Depends(get_current_project),
):
    """Create a billing plan.

    Body:
      plan_id, display_name, description?,
      charge_amount_cents  (what end user pays),
      currency?            (default ETB),
      usage_allocation_cents,
      expiry_days?,
      sort_order?
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    required = {"plan_id", "display_name", "charge_amount_cents",
                "usage_allocation_cents"}
    missing = required - body.keys()
    if missing:
        raise HTTPException(status_code=400, detail={"missing": sorted(missing)})
    try:
        plan = await _billing_plans.create(
            project_id=project.project_id,
            plan_id=str(body["plan_id"]),
            display_name=str(body["display_name"]),
            description=body.get("description"),
            charge_amount_cents=int(body["charge_amount_cents"]),
            usage_allocation_cents=int(body["usage_allocation_cents"]),
            currency=str(body.get("currency") or _SG_DEFAULT_CURRENCY),
            expiry_days=body.get("expiry_days"),
            sort_order=int(body.get("sort_order", 0)),
            processor_price_id=None,  # Chapa: no pre-registered Price
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return {
        "plan": {
            "project_id": plan.project_id,
            "plan_id": plan.plan_id,
            "display_name": plan.display_name,
            "description": plan.description,
            "charge_amount_cents": plan.charge_amount_cents,
            "currency": plan.currency,
            "usage_allocation_cents": plan.usage_allocation_cents,
            "expiry_days": plan.expiry_days,
            "is_active": plan.is_active,
            "sort_order": plan.sort_order,
        }
    }


@app.patch("/billing/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    body: dict,
    project: Project = Depends(get_current_project),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    try:
        plan = await _billing_plans.update(project.project_id, plan_id, **body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail={"error": "No such plan"})
    return {
        "plan": {
            "project_id": plan.project_id,
            "plan_id": plan.plan_id,
            "display_name": plan.display_name,
            "description": plan.description,
            "charge_amount_cents": plan.charge_amount_cents,
            "currency": plan.currency,
            "usage_allocation_cents": plan.usage_allocation_cents,
            "expiry_days": plan.expiry_days,
            "is_active": plan.is_active,
            "sort_order": plan.sort_order,
        }
    }


# -------------------------------------------------------------------
# Checkout — Chapa /transaction/initialize
# -------------------------------------------------------------------

@app.post("/billing/checkout")
async def create_checkout(
    body: dict,
    project: Project = Depends(get_current_project),
):
    """Create a Chapa hosted checkout session for a plan.

    Body:
      plan_id, end_user_id,
      success_url (redirect after Chapa success),
      cancel_url  (redirect after Chapa cancel),
      email?, first_name?, last_name?, phone_number?
    """
    proc = _require_processor()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    required = {"plan_id", "end_user_id", "success_url", "cancel_url"}
    missing = required - body.keys()
    if missing:
        raise HTTPException(status_code=400, detail={"missing": sorted(missing)})

    plan = await _billing_plans.get(project.project_id, str(body["plan_id"]))
    if plan is None:
        raise HTTPException(status_code=404, detail={"error": "No such plan"})
    if not plan.is_active:
        raise HTTPException(status_code=400, detail={"error": "Plan archived"})

    payout = await _payout_accounts.get(project.project_id)
    if payout is None:
        raise HTTPException(status_code=400, detail={
            "error": "No payout account configured for project",
            "next": "POST /billing/payout/onboarding",
        })
    if payout.onboarding_state != "complete":
        raise HTTPException(status_code=400, detail={
            "error": "Payout account not ready (KYC pending)",
            "onboarding_state": payout.onboarding_state,
        })

    callback_url = f"{config.public_base_url}/payment/webhook"

    try:
        result = await proc.create_checkout_session(
            project_id=project.project_id,
            end_user_id=str(body["end_user_id"]),
            processor_account_id=payout.processor_account_id,
            charge_amount=plan.charge,
            success_url=str(body["success_url"]),
            cancel_url=str(body["cancel_url"]),
            callback_url=callback_url,
            plan_id=plan.plan_id,
            usage_allocation=plan.usage_allocation,
            split_type=payout.split_type,
            split_value=payout.split_value,
            email=body.get("email") if isinstance(body.get("email"), str) else None,
            first_name=body.get("first_name") if isinstance(body.get("first_name"), str) else None,
            last_name=body.get("last_name") if isinstance(body.get("last_name"), str) else None,
            phone_number=body.get("phone_number") if isinstance(body.get("phone_number"), str) else None,
        )
    except (ProcessorError, PayoutAccountNotReady, PlatformFeeNotSet, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    # Journal so we can match tx_ref → (project, plan, user) on webhook.
    pool = _db_pool_read
    if pool is not None:
        try:
            await pool.execute(
                """
                INSERT INTO checkout_sessions
                    (session_id, processor, project_id, end_user_id, plan_id,
                     processor_account_id, charge_amount_cents, currency,
                     processor_checkout_url, processor_txn_ref)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (session_id) DO NOTHING
                """,
                result.session_id, config.chapa.processor,
                project.project_id, body["end_user_id"], plan.plan_id,
                payout.processor_account_id,
                plan.charge_amount_cents, plan.currency,
                result.checkout_url, result.processor_txn_ref,
            )
        except Exception:
            pass

    return {
        "session_id": result.session_id,
        "processor": result.processor,
        "checkout_url": result.checkout_url,
        "processor_txn_ref": result.processor_txn_ref,
        "currency": plan.currency,
        "charge_amount_cents": plan.charge_amount_cents,
        "usage_allocation_cents": plan.usage_allocation_cents,
    }


# -------------------------------------------------------------------
# Payment webhook — Chapa (HMAC SHA256 verify-before-process)
# -------------------------------------------------------------------

@app.post("/payment/webhook")
async def payment_webhook(request: Request):
    """Chapa webhook (call_back_url).  Handlers:

      charge.success / checkout.session.completed (depends on Chapa)
         → find checkout_sessions by tx_ref → usage_grants.grant_cents → INCRBY Redis
    """
    proc = _require_processor()
    raw_bytes = await request.body()
    # Starlette lowercases header keys in the request.headers multidict.
    headers = {k: v for k, v in request.headers.items()}

    try:
        payload = await proc.verify_webhook_payload(
            raw_body=raw_bytes, headers=headers,
        )
    except (WebhookVerificationFailed, ProcessorNotConfigured) as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    # Derive a stable processor_event_id (Chapa docs vary on whether the
    # payload always carries a top-level event id — be defensive: prefer
    # payload.event_id or tx_ref or fall back to HASH(processor + body)).
    proc_event_id: str = (
        payload.get("id")
        or payload.get("event_id")
        or (payload.get("data") or {}).get("tx_ref")
        or (payload.get("data") or {}).get("reference")
        or payload.get("tx_ref")
        or None
    )
    if not proc_event_id:
        digest = hashlib.sha256()
        digest.update(config.chapa.processor.encode("utf-8"))
        digest.update(b"|")
        digest.update(raw_bytes)
        proc_event_id = digest.hexdigest()

    event_type = (
        payload.get("event")
        or payload.get("type")
        or (payload.get("data") or {}).get("status")
        or "unknown"
    )
    processor_account_id = (
        payload.get("processor_account_id")
        or (payload.get("meta") or {}).get("processor_account_id")
        or None
    )

    first_delivery = await _payment_events.record(
        processor_event_id=proc_event_id,
        processor=config.chapa.processor,
        event_type=event_type,
        processor_account_id=processor_account_id,
        raw_payload=payload,
    )
    if not first_delivery:
        return JSONResponse({"received": True})

    try:
        await _process_payment_event(event_type, payload)
        await _payment_events.mark_delivered(proc_event_id)
    except Exception as exc:
        await _payment_events.mark_delivered(proc_event_id, error=str(exc))
        return JSONResponse({"received": True, "processing_error": str(exc)}, status_code=200)
    return JSONResponse({"received": True})


async def _process_payment_event(event_type: str, payload: dict) -> None:
    data: dict[str, Any] = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    # Chapa's webhook varies a bit by environment / product — accept the
    # common shapes.  We treat any event with both tx_ref AND status=success
    # as "payment completed → grant".
    tx_ref = (
        payload.get("tx_ref")
        or data.get("tx_ref")
        or data.get("reference")
        or payload.get("reference")
        or None
    )
    status = (
        payload.get("status")
        or data.get("status")
        or event_type
        or ""
    )

    is_success = str(status).lower() in {
        "success", "successful", "completed", "paid", "charge.success",
        "checkout.session.completed",
    }

    if is_success and tx_ref:
        pool = _db_pool_read
        row = None
        if pool is not None:
            row = await pool.fetchrow(
                "SELECT * FROM checkout_sessions WHERE session_id = $1 OR processor_txn_ref = $2",
                str(tx_ref), str(tx_ref),
            )
        if row is None:
            return

        project_id = row["project_id"]
        end_user_id = row["end_user_id"]
        plan_id = row["plan_id"]
        currency = row["currency"] or _SG_DEFAULT_CURRENCY
        actual_cents = int(row["actual_charge_cents"] or row["charge_amount_cents"])

        # Prefer usage_allocation_cents from plan; fall back to charge (defensive).
        plan = await _billing_plans.get(str(project_id), str(plan_id))
        allocation_cents = (
            plan.usage_allocation_cents if plan else int(row["charge_amount_cents"])
        )
        expiry_days = plan.expiry_days if plan else None

        # Resolve processor-side charge id (for ops / refund hooks later).
        charge_id = (
            data.get("id")
            or data.get("charge_id")
            or data.get("payment_id")
            or payload.get("charge_id")
            or None
        )

        grant_id = await _usage_grants.grant_cents(
            project_id=str(project_id),
            end_user_id=str(end_user_id),
            cents=allocation_cents,
            currency=str(currency).upper(),
            plan_id=str(plan_id),
            source_type="checkout",
            source_reference=str(tx_ref),
            processor_charge_id=str(charge_id) if charge_id else None,
            expiry_days=expiry_days,
        )

        # Prime Redis so the user can spend instantly.
        if end_user_id:
            import redis.asyncio as redis
            r = redis.from_url(config.redis_url, decode_responses=True)
            await r.incrby(_end_user_redis_key(str(project_id), str(end_user_id)), allocation_cents)
            await r.close()

        if pool is not None:
            await pool.execute(
                """
                UPDATE checkout_sessions
                SET status = 'completed',
                    actual_charge_cents = $1,
                    usage_grant_id = $2,
                    updated_at = NOW()
                WHERE session_id = $3 OR processor_txn_ref = $4
                """,
                actual_cents,
                uuid.UUID(grant_id) if grant_id else None,
                str(tx_ref), str(tx_ref),
            )
        return


# -------------------------------------------------------------------
# End user balance
# -------------------------------------------------------------------

@app.get("/end-users/{end_user_id}/balance")
async def end_user_balance(
    end_user_id: str,
    project: Project = Depends(get_current_project),
    currency: str = _SG_DEFAULT_CURRENCY,
):
    from_db = await _usage_grants.total_remaining_cents(
        project.project_id, end_user_id, currency=currency.upper(),
    )
    from_redis = await _safe_get_user_balance(project.project_id, end_user_id)
    return {
        "project_id": project.project_id,
        "end_user_id": end_user_id,
        "currency": currency.upper(),
        "remaining_cents_postgres": from_db,
        "remaining_cents_redis": from_redis,
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
