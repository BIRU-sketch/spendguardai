"""
Payment processor integration layer.  PROCESSOR-AGNOSTIC INTERFACE at
the top (PaymentProcessor protocol) + ONE concrete implementation today
(ChapaProcessor).  Next time we add Stripe/PayPal:

  1. Add a StripeProcessor class that implements the same 4 methods
     (create_payout_account, create_checkout_session,
      verify_webhook_payload, processor_name).
  2. Add a factory at the bottom that reads payout_accounts.processor
     / config.chapa.processor and returns the right instance.
  3. DONE — no caller changes needed in main.py, billing_plans.py, etc.

Chapa endpoints we talk to:
  POST https://api.chapa.co/v1/subaccount
       → create developer subaccount (SpendGuard calls on founder's
          behalf with bank details the founder submitted to SpendGuard).
  POST https://api.chapa.co/v1/transaction/initialize
       → create hosted checkout session with subaccount split applied.
  POST <our callback_url>  (Chapa calls us — verify_webhook_payload())

Authorizations:
  All outbound calls use `Authorization: Bearer CHAPA_SECRET_KEY`
  (test mode keys start with `CHASECK_TEST-`; live with `CHASECK_LIVE-`).
Webhook verification:
  Chapa computes `HMAC-SHA256(raw_body, CHAPA_WEBHOOK_HMAC_SECRET)` and
  puts it in the `x-chapa-signature` header.  We re-compute and compare
  in constant time before processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Protocol

import httpx

from app.config import config
from app.pricing import (
    Money,
    PlatformFeeSettings,
    _cents as _round_to_int_cents,
    _resolve_fee_percent_from_env_or_raise,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProcessorError(Exception):
    """Base class for any failure the caller can handle."""


class ProcessorNotConfigured(ProcessorError):
    """Raised when env keys are missing so a processor call is impossible."""


class PayoutAccountNotReady(ProcessorError):
    """Raised when we try to create a checkout session for a project whose
    payout_account is not in onboarding_state='complete'."""


class PlatformFeeNotSet(ProcessorError):
    """Raised at checkout-time if platform_settings.platform_fee_percent
    is NULL AND the env fallback is also unset."""


class WebhookVerificationFailed(ProcessorError):
    """Raised when a webhook payload's signature/HMAC doesn't match."""


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PayoutAccountResult:
    """Result of create_payout_account (Chapa: POST /subaccount)."""

    processor: str                    # 'chapa'
    processor_account_id: str         # Chapa's subaccount id
    onboarding_state: str             # 'pending' / 'complete' / 'rejected'


@dataclass(frozen=True)
class CheckoutResult:
    """Result of create_checkout_session."""

    session_id: str                   # our generated tx_ref (primary key of checkout_sessions)
    processor: str                    # 'chapa'
    checkout_url: str                 # redirect user here
    processor_txn_ref: str | None     # Chapa's tx_ref if different from our session_id


# ---------------------------------------------------------------------------
# Processor protocol — every processor implementation implements this, and
# every caller (main.py endpoints, sync_to_stripe → future sync_to_processor)
# only talks through this protocol.
# ---------------------------------------------------------------------------

class PaymentProcessor(Protocol):
    @property
    def name(self) -> str: ...  # 'chapa', 'stripe', ...

    async def create_payout_account(
        self,
        *,
        project_id: str,
        business_name: str,
        bank_code: str,
        account_number: str,
        account_name: str,
        split_type: str,
        split_value: Decimal,
        email: str | None = None,
    ) -> PayoutAccountResult: ...

    async def create_checkout_session(
        self,
        *,
        project_id: str,
        end_user_id: str,
        processor_account_id: str,
        charge_amount: Money,
        success_url: str,
        cancel_url: str,
        callback_url: str,          # webhook endpoint — Chapa's callback_url
        plan_id: str,
        usage_allocation: Money,
        split_type: str,            # 'percentage' | 'flat' (from payout_accounts)
        split_value: Decimal,       # 0.30 (= 30%) for percentage; cents for flat
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone_number: str | None = None,
    ) -> CheckoutResult: ...

    async def verify_webhook_payload(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Verify the webhook payload and return the parsed JSON.
        Raises WebhookVerificationFailed on any mismatch."""
        ...


# ---------------------------------------------------------------------------
# DB-backed take-rate (unchanged concept — kept here so callers don't have
# to know where the setting lives).
# ---------------------------------------------------------------------------

class DatabasePlatformFeeSettings:
    """Reads platform_settings.platform_fee_percent, cached for 30s."""

    def __init__(self, pool_getter) -> None:  # type: ignore[no-untyped-def]
        self._pool_getter = pool_getter
        self._cached_at: float | None = None
        self._cached_value: Decimal | None = None
        self._ttl_seconds: float = 30.0

    async def get_platform_fee_percent(self) -> Decimal | None:
        now = time.time()
        if self._cached_at is not None and (now - self._cached_at) < self._ttl_seconds:
            return self._cached_value
        pool = self._pool_getter()
        if pool is None:
            return _resolve_fee_percent_from_env_or_raise()
        try:
            row = await pool.fetchrow(
                "SELECT platform_fee_percent FROM platform_settings WHERE id = 1"
            )
        except Exception:
            return _resolve_fee_percent_from_env_or_raise()
        db_val = row["platform_fee_percent"] if row else None
        result: Decimal | None
        if db_val is None:
            result = _resolve_fee_percent_from_env_or_raise()
        else:
            try:
                result = Decimal(str(db_val))
            except Exception:
                result = _resolve_fee_percent_from_env_or_raise()
        self._cached_value = result
        self._cached_at = now
        return result


# ---------------------------------------------------------------------------
# Chapa implementation
# ---------------------------------------------------------------------------

_CHAPA_BASE_URL = "https://api.chapa.co/v1"


class ChapaProcessor:
    """Implements PaymentProcessor for Chapa (Ethiopian payment gateway).

    Chapa docs:
      Subaccounts  → https://developer.chapa.co/docs/subaccounts
      Initialize   → https://developer.chapa.co/docs/initialize
      Webhooks     → https://developer.chapa.co/docs/webhooks
    """

    name = "chapa"

    def __init__(
        self,
        *,
        settings: PlatformFeeSettings,
    ) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_secrets(self) -> tuple[str, str | None]:
        secret = config.chapa.secret_key
        if not secret:
            raise ProcessorNotConfigured(
                "CHAPA_SECRET_KEY is not set.  Configure it in .env to "
                "enable Chapa-powered microbilling."
            )
        return secret, config.chapa.webhook_hmac_secret

    def _client(self) -> httpx.AsyncClient:
        secret, _ = self._require_secrets()
        return httpx.AsyncClient(
            base_url=_CHAPA_BASE_URL,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    # ------------------------------------------------------------------
    # create_payout_account  →  Chapa POST /subaccount
    # ------------------------------------------------------------------

    async def create_payout_account(
        self,
        *,
        project_id: str,
        business_name: str,
        bank_code: str,
        account_number: str,
        account_name: str,
        split_type: str,
        split_value: Decimal,
        email: str | None = None,
    ) -> PayoutAccountResult:
        if split_type not in ("percentage", "flat"):
            raise ProcessorError(
                f"Chapa split_type must be 'percentage' or 'flat', got {split_type!r}"
            )
        if split_value < 0:
            raise ProcessorError(f"split_value negative: {split_value}")

        # Convert internal split_value to Chapa's POST /subaccount format.
        # Per https://developer.chapa.co/docs/subaccounts :
        #   split_value is a STRING.
        #     percentage → whole-number percent, e.g. "30" means 30%.
        #                   (Internal rep: 0.30  → ×100 → "30")
        #     flat       → amount in BIRR, e.g. "20" means 20 ETB birr.
        #                   (Internal rep: 2000 cents ÷ 100 → "20.00")
        if split_type == "percentage":
            chapa_split_value = str(_round_to_int_cents(Decimal(split_value) * Decimal("100")))
        else:  # flat
            chapa_split_value = str(
                (Decimal(split_value) / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

        payload: dict[str, Any] = {
            "business_name": business_name,
            "account_name": account_name,
            "bank_code": bank_code,
            "account_number": account_number,
            "split_type": split_type,
            "split_value": chapa_split_value,  # string, per Chapa spec
        }
        if email:
            payload["email"] = email

        async with self._client() as c:
            try:
                resp = await c.post("/subaccount", json=payload)
            except httpx.HTTPError as exc:
                raise ProcessorError(f"Chapa subaccount network error: {exc}") from exc
            if resp.status_code >= 400:
                raise ProcessorError(
                    f"Chapa POST /subaccount failed {resp.status_code}: {resp.text}"
                )
            body = resp.json()

        # Chapa success body: {"message": "...", "data": {"subaccount_id": "subacc_..."}}
        data = body.get("data") or {}
        subacc_id = data.get("subaccount_id") or data.get("id") or body.get("subaccount_id")
        if not subacc_id:
            raise ProcessorError(
                f"Chapa POST /subaccount response missing subaccount_id: {body!r}"
            )

        # Chapa subaccounts typically need KYC review before charges are
        # live.  We pessimistically mark 'pending'; the operator can flip
        # to 'complete' via a dashboard or an admin endpoint once Chapa
        # confirms.  (Chapa doesn't yet have a great async account KYC
        # webhook we know of, so this is a conservative default.)
        return PayoutAccountResult(
            processor="chapa",
            processor_account_id=str(subacc_id),
            onboarding_state="pending",
        )

    # ------------------------------------------------------------------
    # create_checkout_session  →  Chapa POST /transaction/initialize
    # ------------------------------------------------------------------

    async def create_checkout_session(
        self,
        *,
        project_id: str,
        end_user_id: str,
        processor_account_id: str,
        charge_amount: Money,
        success_url: str,
        cancel_url: str,
        callback_url: str,
        plan_id: str,
        usage_allocation: Money,
        split_type: str,
        split_value: Decimal,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone_number: str | None = None,
    ) -> CheckoutResult:
        # Money invariants — never mix currencies on the way out.
        if charge_amount.currency != usage_allocation.currency:
            raise ProcessorError(
                f"create_checkout_session currency mix: charge="
                f"{charge_amount.currency}, allocation={usage_allocation.currency}"
            )
        if charge_amount.cents < usage_allocation.cents:
            raise ProcessorError(
                f"create_checkout_session: usage_allocation exceeds charge"
            )
        if not processor_account_id:
            raise PayoutAccountNotReady(
                "Project has no processor_account_id yet — complete the "
                "payout account onboarding first."
            )
        if split_type not in ("percentage", "flat"):
            raise ProcessorError(
                f"split_type must be 'percentage' or 'flat', got {split_type!r}"
            )
        if split_value < 0:
            raise ProcessorError(f"split_value negative: {split_value}")

        # tx_ref = our session id.  Generate deterministically unique:
        tx_ref = f"sg-{config.chapa.processor}-{uuid.uuid4().hex[:20]}"

        # Chapa amount: if currency is ETB, amount is in ETB *BIRR* (not
        # cents).  This is the exact opposite of Stripe's "always lowest
        # denomination" rule.  So:
        #   charge_amount.cents (1000 ETB cents = 10 ETB birr) → amount: 10.00
        amount_birr = Decimal(charge_amount.cents) / Decimal("100")

        # Convert payout split to Chapa's subaccount share format.
        #
        # Chapa Initialize → subaccounts: [{id, share_type, share}]
        #   share_type:
        #     "percentage" → share = whole-number percent, e.g. "30" = 30%.
        #     "flat"       → share = amount in BIRR (same currency as charge),
        #                    e.g. "5.00" = 5 ETB birr flat to the subaccount.
        #
        # Our internal rep:
        #   split_type="percentage", split_value = 0.30  → share="30"  (×100)
        #   split_type="flat",       split_value = 500   → share="5.00" (÷100,
        #                                                     cents → birr)
        if split_type == "percentage":
            chapa_share = str(_round_to_int_cents(Decimal(split_value) * Decimal("100")))
        else:  # flat
            chapa_share = str(
                (Decimal(split_value) / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

        payload: dict[str, Any] = {
            "amount": str(amount_birr),
            "currency": charge_amount.currency,
            "email": email or f"user+{end_user_id}@spendguard.local",
            "first_name": first_name or "",
            "last_name": last_name or "",
            "phone_number": phone_number or "",
            "tx_ref": tx_ref,
            "callback_url": callback_url,
            "return_url": success_url,
            # customization
            "title": config.chapa.platform_display_name,
            "description": f"Plan {plan_id} — {usage_allocation.cents}{charge_amount.currency} cents AI credit",
            # Subaccount split — Chapa applies the payout_account's
            # split_type/split_value on this specific transaction.
            # Per https://developer.chapa.co/docs/payment-split :
            #   subaccounts = ARRAY of {id, share_type, share}
            "subaccounts": [
                {
                    "id": processor_account_id,
                    "share_type": split_type,
                    "share": chapa_share,
                },
            ],
            # metadata survives to the webhook so we don't have to
            # look things up solely by tx_ref.
            "meta": {
                "project_id": project_id,
                "end_user_id": end_user_id,
                "plan_id": plan_id,
                "charge_amount_cents": charge_amount.cents,
                "usage_allocation_cents": usage_allocation.cents,
                "currency": charge_amount.currency,
            },
        }

        async with self._client() as c:
            try:
                resp = await c.post("/transaction/initialize", json=payload)
            except httpx.HTTPError as exc:
                raise ProcessorError(f"Chapa initialize network error: {exc}") from exc
            if resp.status_code >= 400:
                raise ProcessorError(
                    f"Chapa POST /transaction/initialize failed {resp.status_code}: {resp.text}"
                )
            body = resp.json()

        data = body.get("data") or {}
        checkout_url = data.get("checkout_url") or body.get("checkout_url")
        if not checkout_url:
            raise ProcessorError(
                f"Chapa initialize response missing checkout_url: {body!r}"
            )
        # Chapa returns our tx_ref back sometimes as data.tx_ref; if not,
        # trust what we sent.
        remote_ref = data.get("tx_ref") or tx_ref

        return CheckoutResult(
            session_id=tx_ref,
            processor="chapa",
            checkout_url=str(checkout_url),
            processor_txn_ref=str(remote_ref) if remote_ref else None,
        )

    # ------------------------------------------------------------------
    # verify_webhook_payload — HMAC-SHA256 constant-time compare
    # ------------------------------------------------------------------

    async def verify_webhook_payload(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        _, webhook_secret = self._require_secrets()
        if not webhook_secret:
            # HMAC secret missing in env — fail closed in the processor.
            # Callers can override by inspecting ProcessorNotConfigured
            # and allowing in dev if they really want to (not recommended).
            raise ProcessorNotConfigured(
                "CHAPA_WEBHOOK_HMAC_SECRET is not set.  Cannot verify "
                "webhook payloads without it."
            )

        # Chapa header is case-insensitive; FastAPI headers are lowercased
        # by Starlette so look up both.
        expected_sig = (
            headers.get("x-chapa-signature")
            or headers.get("X-Chapa-Signature")
            or ""
        )
        if not expected_sig:
            raise WebhookVerificationFailed(
                "Missing x-chapa-signature header on webhook payload"
            )

        computed = hmac.new(
            key=webhook_secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(computed, expected_sig):
            raise WebhookVerificationFailed(
                "Chapa webhook HMAC signature did not match — rejecting"
            )

        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise WebhookVerificationFailed(
                f"Chapa webhook JSON parse failed: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Factory — given a processor name (or a payout_accounts.processor column)
# return the right PaymentProcessor implementation.
# ---------------------------------------------------------------------------

_PROCESSOR_REGISTRY: dict[str, type[ChapaProcessor]] = {
    "chapa": ChapaProcessor,
}


def build_processor(
    processor_name: str | None = None,
    *,
    settings: PlatformFeeSettings,
) -> PaymentProcessor:
    """Factory: build the active processor for the SpendGuard instance.

    Defaults to config.chapa.processor (env PAYMENT_PROCESSOR or 'chapa')
    if processor_name is None."""
    name = (processor_name or config.chapa.processor).strip().lower()
    if name not in _PROCESSOR_REGISTRY:
        raise ProcessorNotConfigured(
            f"Unknown payment processor {name!r}.  "
            f"Registered: {sorted(_PROCESSOR_REGISTRY)}"
        )
    cls = _PROCESSOR_REGISTRY[name]
    return cls(settings=settings)
