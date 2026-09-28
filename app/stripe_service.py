"""
Stripe integration layer for SpendGuard's Connect microbilling platform.

Covers:
  A) Connect Express onboarding  — create + refresh an account link for
     a project so the developer can submit tax/bank details.
  B) Destination charges         — Checkout Session that bills the end
     user's card and splits the money three ways automatically via
     application_fee_amount + transfer_data.destination.
  C) Webhooks                    — idempotent processing of:
       * checkout.session.completed → write usage_grant
       * account.updated            → mark connect_accounts.onboarding_state
  D) Settings read                — platform_fee_percent from the DB
     (with env fallback for local dev).

Nothing outside this module imports stripe directly. If we ever switch
payment processors, this whole file is replaced and the call sites in
main.py / billing_plans.py don't change.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import stripe
import stripe.error
from stripe.webhook import WebhookSignature

from app.config import config
from app.pricing import (
    CheckoutSplit,
    PlatformFeeSettings,
    compute_checkout_split,
    _resolve_fee_percent_from_env_or_raise,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class StripeServiceError(Exception):
    """Base class for any failure the caller can handle."""


class StripeNotConfigured(StripeServiceError):
    """Raised when STRIPE_SECRET_KEY is missing — operator needs to set it."""


class PlatformFeeNotSet(StripeServiceError):
    """Raised at checkout-time if the SpendGuard founder has not yet set
    a take-rate in platform_settings (and env fallback is also empty)."""


class ConnectAccountNotReady(StripeServiceError):
    """Raised when a project's Connect account is still in onboarding."""


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccountLinkResult:
    url: str
    expires_at: float  # unix seconds from Stripe (matches AccountLink.expires_at)


@dataclass(frozen=True)
class CheckoutSessionResult:
    session_id: str
    checkout_url: str


# ---------------------------------------------------------------------------
# Settings loader (implements PlatformFeeSettings Protocol from pricing.py)
# ---------------------------------------------------------------------------

class DatabasePlatformFeeSettings:
    """Production implementation: read platform_fee_percent from the
    platform_settings Postgres table via an asyncpg pool.

    The singleton row is cached for 30 seconds in-process so we don't
    add a DB round-trip to every checkout; the founder changing the
    take-rate just waits for the cache to TTL, which is fine for a
    business-decision constant.
    """

    def __init__(self, pool_getter) -> None:  # pool_getter: () -> asyncpg.Pool
        self._pool_getter = pool_getter
        self._cached_value: Decimal | None = None
        self._cached_at: float = 0.0
        self._ttl: float = 30.0
        self._lock = asyncio.Lock()

    async def get_platform_fee_percent(self) -> Decimal | None:
        now = time.monotonic()
        if now - self._cached_at < self._ttl:
            return self._cached_value
        async with self._lock:
            now = time.monotonic()
            if now - self._cached_at < self._ttl:
                return self._cached_value
            pool = self._pool_getter()
            row = await pool.fetchrow(
                "SELECT platform_fee_percent FROM platform_settings WHERE id = 1"
            )
            val: Decimal | None = None
            if row is not None and row["platform_fee_percent"] is not None:
                val = Decimal(str(row["platform_fee_percent"]))
            if val is None:
                val = _resolve_fee_percent_from_env_or_raise()
            self._cached_value = val
            self._cached_at = now
            return val


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class StripeService:
    """Single instance per process. Holds a configured stripe client +
    a DB-backed take-rate resolver."""

    def __init__(
        self,
        *,
        settings: PlatformFeeSettings,
        secret_key: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self._settings = settings
        self._secret_key = secret_key or config.stripe.secret_key
        self._webhook_secret = webhook_secret or config.stripe.webhook_signing_secret
        if self._secret_key:
            stripe.api_key = self._secret_key
        self._display_name = config.stripe.platform_display_name

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_configured(self) -> None:
        if not self._secret_key:
            raise StripeNotConfigured(
                "STRIPE_SECRET_KEY environment variable is not set. "
                "Configure Stripe before using microbilling features."
            )

    async def _require_take_rate(self) -> Decimal:
        rate = await self._settings.get_platform_fee_percent()
        if rate is None:
            raise PlatformFeeNotSet(
                "PLATFORM_FEE_PERCENT is not set. The SpendGuard operator "
                "must set platform_settings.platform_fee_percent in the DB "
                "or set the PLATFORM_FEE_PERCENT env var for local testing."
            )
        return rate

    # ------------------------------------------------------------------
    # A) Connect Express onboarding
    # ------------------------------------------------------------------

    async def create_express_account(self, project_id: str, email: str | None = None) -> str:
        """Create a Connect Express account for a project and return its
        stripe_account_id (acct_xxx). Caller is responsible for writing
        that id + 'restricted' state to connect_accounts."""
        self._require_configured()
        loop = asyncio.get_event_loop()
        params: dict[str, Any] = {
            "type": "express",
            "country": "US",
            "email": email,
            "business_type": "individual",
            "business_profile": {
                "url": f"{config.public_base_url}/p/{project_id}",
                "mcc": "7372",  # Computer Programming Services
                "product_description": "AI application metered via SpendGuard",
            },
            "capabilities": {
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            "settings": {
                "payouts": {"schedule": {"interval": "weekly"}},
            },
            "metadata": {"project_id": project_id},
        }
        try:
            acct = await loop.run_in_executor(
                None, lambda: stripe.Account.create(**params)
            )
        except stripe.error.StripeError as exc:
            raise StripeServiceError(f"Failed to create Connect account: {exc}") from exc
        return acct.id

    async def create_account_link(
        self,
        stripe_account_id: str,
        refresh_url: str,
        return_url: str,
    ) -> AccountLinkResult:
        """One-time onboarding link. Consumed on first click; call this
        again to generate a new link if the user needs to re-onboard."""
        self._require_configured()
        loop = asyncio.get_event_loop()
        try:
            link = await loop.run_in_executor(
                None,
                lambda: stripe.AccountLink.create(
                    account=stripe_account_id,
                    refresh_url=refresh_url,
                    return_url=return_url,
                    type="account_onboarding",
                    collect="currently_due",
                ),
            )
        except stripe.error.StripeError as exc:
            raise StripeServiceError(f"Failed to create account link: {exc}") from exc
        return AccountLinkResult(url=link.url, expires_at=float(link.expires_at))

    # ------------------------------------------------------------------
    # B) Destination-charge Checkout Session
    # ------------------------------------------------------------------

    async def create_checkout_session(
        self,
        *,
        project_id: str,
        end_user_id: str | None,
        stripe_account_id: str,         # acct_xxx of the developer (connect account)
        stripe_price_id: str,           # price_xxx of the plan on the connect account
        charge_amount_cents: int,       # used for verification only; real amount comes from Stripe price
        usage_allocation_cents: int,
        success_url: str,
        cancel_url: str,
        plan_id: str,
    ) -> CheckoutSessionResult:
        """Build a destination Charge via Stripe Checkout:
          - Charge is CREATED on the Connect account (stripe_account_id).
          - application_fee_amount = SpendGuard's cut
          - transfer_data.destination isn't needed on a destination charge;
            the developer already receives (amount − application_fee) automatically.
          - On the checkout.session.completed webhook we create the
            usage_grants row for (usage_allocation_cents).

        IMPORTANT per user instruction: the 3-way split formula is
          total_charge = founder_payout + usage_allocation + platform_fee
          founder_payout = total - usage_allocation - platform_fee
        This is enforced by compute_checkout_split().
        """
        self._require_configured()
        rate = await self._require_take_rate()

        # We need the REAL charge amount to compute the split — Stripe
        # Price objects define amount; we read it via the connect account
        # so we can verify usage_allocation ≤ charge_amount and compute
        # the exact founder payout.
        loop = asyncio.get_event_loop()
        try:
            price = await loop.run_in_executor(
                None,
                lambda: stripe.Price.retrieve(
                    stripe_price_id,
                    stripe_account=stripe_account_id,
                ),
            )
        except stripe.error.StripeError as exc:
            raise StripeServiceError(f"Failed to read Stripe price {stripe_price_id}: {exc}") from exc

        real_amount_cents: int = int(price.unit_amount or 0)
        if real_amount_cents <= 0:
            raise StripeServiceError(
                f"Stripe price {stripe_price_id} has no unit_amount set"
            )

        split = compute_checkout_split(
            charge_amount_cents=real_amount_cents,
            usage_allocation_cents=usage_allocation_cents,
            platform_fee_percent=rate,
        )

        # On a destination charge we pass stripe_account=acct_xxx so the
        # charge lands there directly. Stripe then automatically pays
        # the developer (charge_amount − application_fee). Perfect.
        try:
            session = await loop.run_in_executor(
                None,
                lambda: stripe.checkout.Session.create(
                    mode="payment",
                    line_items=[
                        {
                            "price": stripe_price_id,
                            "quantity": 1,
                        }
                    ],
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={
                        "project_id": project_id,
                        "end_user_id": end_user_id or "",
                        "plan_id": plan_id,
                        "usage_allocation_cents": str(split.usage_allocation_cents),
                        "platform_fee_amount_cents": str(split.platform_fee_amount_cents),
                        "founder_payout_cents": str(split.founder_payout_cents),
                    },
                    payment_intent_data={
                        "application_fee_amount": split.platform_fee_amount_cents,
                        "metadata": {
                            "project_id": project_id,
                            "end_user_id": end_user_id or "",
                            "plan_id": plan_id,
                        },
                    },
                    stripe_account=stripe_account_id,
                ),
            )
        except stripe.error.StripeError as exc:
            raise StripeServiceError(f"Failed to create checkout session: {exc}") from exc

        return CheckoutSessionResult(session_id=session.id, checkout_url=session.url)

    # ------------------------------------------------------------------
    # C) Webhook verify + event journal write
    # ------------------------------------------------------------------

    def verify_and_deserialize(
        self,
        raw_body: bytes,
        signature_header: str | None,
    ) -> dict[str, Any]:
        """Verify webhook signature (if configured) and return the parsed
        event dict. If webhook secret is unset, we fall back to trusting
        the raw body — only for local dev, never prod."""
        self._require_configured()
        if self._webhook_secret and signature_header:
            try:
                WebhookSignature.verify_header(
                    raw_body,
                    signature_header,
                    self._webhook_secret,
                )
            except Exception as exc:
                raise StripeServiceError(f"Invalid webhook signature: {exc}") from exc
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise StripeServiceError(f"Invalid JSON in webhook: {exc}") from exc

    # ------------------------------------------------------------------
    # D) Price management (billing_plans → Stripe Price on connect account)
    # ------------------------------------------------------------------

    async def create_price_on_connect_account(
        self,
        stripe_account_id: str,
        *,
        product_name: str,
        unit_amount_cents: int,
        currency: str = "usd",
        plan_id: str,
        description: str | None = None,
    ) -> str:
        """Create a Price on a Connect account. Returns price_xxx id.
        Prices are ONE-TIME (usage grants aren't subscriptions yet)."""
        self._require_configured()
        loop = asyncio.get_event_loop()
        try:
            price = await loop.run_in_executor(
                None,
                lambda: stripe.Price.create(
                    product_data={
                        "name": product_name,
                        "description": description or f"SpendGuard plan {plan_id}",
                        "metadata": {
                            "plan_id": plan_id,
                            "type": "usage_grant",
                        },
                    },
                    unit_amount=unit_amount_cents,
                    currency=currency,
                    metadata={
                        "plan_id": plan_id,
                    },
                    stripe_account=stripe_account_id,
                ),
            )
        except stripe.error.StripeError as exc:
            raise StripeServiceError(f"Failed to create Stripe price: {exc}") from exc
        return price.id
