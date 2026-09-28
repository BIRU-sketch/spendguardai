"""
Pricing engine — the ONLY place money math happens.

TWO MONEY TYPES:
  1. Money(cents: int, currency: str)  — the currency-aware bag.  Every
     comparison, sum, and split MUST go through this type's operators
     (or the helper functions below) so we NEVER silently add ETB cents
     to USD cents.

  2. Per-request token-cost math  (unchanged concept from before — but
     TokenCost now also carries currency).

CHAPA-SPECIFIC SPLIT RULE (enforced at billing_plan write-time, NOT at
checkout — by the time a user clicks "pay" the plan is pre-validated):

  Given a plan's charge_amount and the project's payout_accounts split:
      founder_payout = charge_amount * split_value   (if percentage)
                   OR = split_value (flat, in same currency cents)
      platform_remainder = charge_amount − founder_payout
      REJECT the plan if usage_allocation_cents > platform_remainder

  Once a plan passes that check, checkout-time just trusts it.  The
  existing compute_checkout_split is ALSO kept for defense-in-depth at
  checkout time (never trust anything from an HTTP round-trip).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from app.config import PLATFORM_FEE_ENV_FALLBACK


# ---------------------------------------------------------------------------
# Money — currency-aware integer cents.  Currency is ISO 4217 CHAR(3),
# uppercase-normalized on construction.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Money:
    """Immutable (cents, currency) pair.  All arithmetic checks that
    currencies match before returning a new Money."""

    cents: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise ValueError(f"Money.cents must be int, got {type(self.cents).__name__}")
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError(
                f"Money.currency must be a 3-letter ISO 4217 code (got {self.currency!r})"
            )
        # Normalize once: uppercase.  Accept 'etb' in input → store 'ETB'.
        object.__setattr__(self, "currency", self.currency.upper())

    # ------------------------------------------------------------------
    # Arithmetic helpers — never mix currencies.
    # ------------------------------------------------------------------

    def _require_same_currency(self, other: "Money", op: str) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Currency mismatch in Money.{op}: "
                f"{self.currency} vs {other.currency}"
            )

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other, "__add__")
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._require_same_currency(other, "__sub__")
        return Money(self.cents - other.cents, self.currency)

    def __mul__(self, factor: Decimal | int) -> "Money":
        if isinstance(factor, int):
            return Money(self.cents * factor, self.currency)
        cents = _cents(Decimal(self.cents) * Decimal(factor))
        return Money(cents, self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._require_same_currency(other, "__lt__")
        return self.cents < other.cents

    def __le__(self, other: "Money") -> bool:
        self._require_same_currency(other, "__le__")
        return self.cents <= other.cents

    def __gt__(self, other: "Money") -> bool:
        self._require_same_currency(other, "__gt__")
        return self.cents > other.cents

    def __ge__(self, other: "Money") -> bool:
        self._require_same_currency(other, "__ge__")
        return self.cents >= other.cents

    def __neg__(self) -> "Money":
        return Money(-self.cents, self.currency)

    def is_zero(self) -> bool:
        return self.cents == 0

    def __str__(self) -> str:  # pragma: no cover — pretty for logs/errors
        # e.g. 1000 ETB cents → "ETB 10.00"
        whole, frac = divmod(self.cents, 100)
        return f"{self.currency} {whole}.{frac:02d}"


def sum_money(items: list[Money], expected_currency: str | None = None) -> Money:
    """Sum a list of Money values, raising CurrencyMismatch on any mix.
    If expected_currency is given, every item must match it."""
    if not items:
        if expected_currency is None:
            raise ValueError("sum_money() with empty list needs expected_currency")
        return Money(0, expected_currency)
    cur = expected_currency or items[0].currency
    cur = cur.upper()
    total = 0
    for m in items:
        if m.currency != cur:
            raise ValueError(
                f"sum_money() currency mix: expected {cur}, got {m.currency}"
            )
        total += m.cents
    return Money(total, cur)


# ---------------------------------------------------------------------------
# Data shapes (unchanged concept, some Money-ified below)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenCost:
    """Cost of one request, expressed in CENTS.  High precision so
    individual draws from micro-tokens don't drift; the consumer
    rounds to integer cents only when drawing from a usage_grant row."""

    input_cost_cents: Decimal
    output_cost_cents: Decimal
    flat_fee_cents: Decimal
    currency: str = "ETB"

    @property
    def total_cents(self) -> Decimal:
        return self.input_cost_cents + self.output_cost_cents + self.flat_fee_cents

    def as_money(self) -> Money:
        return Money(_cents(self.total_cents), self.currency)


@dataclass(frozen=True)
class PriceCard:
    """Per-model pricing.  Currency is implicit (same currency the
    SpendGuard instance is operating in); callers wrap results in
    TokenCost with the right currency tag."""

    price_per_1k_input: Decimal
    price_per_1k_output: Decimal
    flat_fee_cents: Decimal = Decimal("0")


@dataclass(frozen=True)
class CheckoutSplit:
    """3-way split of a single charge, ALL in the same currency.
    (Currency is a single tag on the charge — it's enforced at
    construction that every field matches.)"""

    charge: Money
    usage_allocation: Money
    platform_fee: Money
    founder_payout: Money

    def verify(self) -> None:
        # Currency match
        cur = self.charge.currency
        for f in (self.usage_allocation, self.platform_fee, self.founder_payout):
            if f.currency != cur:
                raise ValueError(
                    f"CheckoutSplit currency mix: charge={cur}, "
                    f"founder={self.founder_payout.currency}"
                )
        # Addition invariant
        total = self.usage_allocation + self.platform_fee + self.founder_payout
        if total.cents != self.charge.cents:
            raise ValueError(
                f"CheckoutSplit math invariant broken: "
                f"{self.usage_allocation.cents}(usage) + "
                f"{self.platform_fee.cents}(platform) + "
                f"{self.founder_payout.cents}(founder) = {total.cents} "
                f"≠ charge {self.charge.cents} ({cur})"
            )
        # Non-negativity
        if any(v.cents < 0 for v in (
            self.charge, self.usage_allocation, self.platform_fee, self.founder_payout,
        )):
            raise ValueError(f"CheckoutSplit contains negative amounts: {self}")


# ---------------------------------------------------------------------------
# Platform take-rate resolver (unchanged)
# ---------------------------------------------------------------------------

class PlatformFeeSettings(Protocol):
    async def get_platform_fee_percent(self) -> Decimal | None: ...


def _cents(decimal_value: Decimal) -> int:
    """Round half-up to integer cents."""
    return int(Decimal(decimal_value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_checkout_split(
    charge_amount: Money,
    usage_allocation: Money,
    platform_fee_percent: Decimal,
) -> CheckoutSplit:
    """Pure function — compute the 3-way split of one charge.

    Preconditions (checked):
      • charge  ≥ usage_allocation  (same currency)
      • 0 ≤ platform_fee_percent < 1
    Math:
        platform_fee  = round(charge × take_rate)
        founder_payout = charge − usage_allocation − platform_fee
    Raises a semantic ValueError (not a generic assertion) before
    CheckoutSplit construction if the numbers force a negative founder.
    """

    if charge_amount.currency != usage_allocation.currency:
        raise ValueError(
            f"compute_checkout_split charge/usage currency mismatch: "
            f"{charge_amount.currency} vs {usage_allocation.currency}"
        )
    if charge_amount.cents < 0:
        raise ValueError(f"charge_amount negative: {charge_amount}")
    if usage_allocation.cents < 0:
        raise ValueError(f"usage_allocation negative: {usage_allocation}")
    if usage_allocation > charge_amount:
        raise ValueError(
            f"usage_allocation ({usage_allocation}) exceeds charge_amount "
            f"({charge_amount}) — plan misconfigured"
        )
    if not (Decimal("0") <= platform_fee_percent < Decimal("1")):
        raise ValueError(
            f"platform_fee_percent out of range [0,1): {platform_fee_percent}"
        )

    platform_fee_dec = Decimal(charge_amount.cents) * Decimal(platform_fee_percent)
    platform_fee_cents = _cents(platform_fee_dec)
    platform_fee = Money(platform_fee_cents, charge_amount.currency)

    if (usage_allocation.cents + platform_fee_cents) > charge_amount.cents:
        raise ValueError(
            f"usage_allocation_cents ({usage_allocation.cents}) + "
            f"platform_fee_amount_cents ({platform_fee_cents}) = "
            f"{usage_allocation.cents + platform_fee_cents} > "
            f"charge_amount_cents ({charge_amount.cents}). "
            f"Either reduce usage_allocation_cents on the plan, reduce "
            f"PLATFORM_FEE_PERCENT (currently {platform_fee_percent}), or "
            f"raise the charge_amount so the split doesn't force a "
            f"negative founder payout."
        )

    founder = Money(
        charge_amount.cents - usage_allocation.cents - platform_fee_cents,
        charge_amount.currency,
    )

    split = CheckoutSplit(
        charge=charge_amount,
        usage_allocation=usage_allocation,
        platform_fee=platform_fee,
        founder_payout=founder,
    )
    split.verify()
    return split


def _resolve_fee_percent_from_env_or_raise() -> Decimal | None:
    return PLATFORM_FEE_ENV_FALLBACK


# ---------------------------------------------------------------------------
# PLAN VALIDATION — enforce the Chapa split rule AT PLAN WRITE TIME so the
# operator/founder sees the exact numbers (charge, founder, remainder,
# usage_alloc) in the error, instead of failing later at checkout-time.
#
# This function doesn't touch the DB — it's pure, so billing_plans.py and
# the HTTP layer and any future CLI can call it.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PayoutSplit:
    """How much of each charge goes to the founder.  Produced by the
    payout_accounts row and consumed by the plan validator."""

    split_type: str        # 'percentage' or 'flat'
    split_value: Decimal   # Decimal fraction (0.30 = 30%) for percentage;
                           # integer cents for flat (but stored as Decimal
                           # so the caller doesn't need two types)
    currency: str          # currency the flat/charge lives in


@dataclass(frozen=True)
class PlanValidationResult:
    """Result of validate_billing_plan_split().  Either ok=True, or
    ok=False with the four component lines so the caller can render a
    specific error."""

    ok: bool
    charge: Money
    founder_payout: Money
    platform_remainder: Money
    usage_allocation: Money
    error_message: str | None = None


def compute_founder_payout(charge: Money, split: PayoutSplit) -> Money:
    """Given a charge and a project's payout split, return the Money the
    founder receives (same currency as charge)."""
    if split.currency.upper() != charge.currency:
        raise ValueError(
            f"compute_founder_payout: split.currency ({split.currency}) "
            f"≠ charge.currency ({charge.currency})"
        )
    if split.split_type == "percentage":
        cents = _cents(Decimal(charge.cents) * Decimal(split.split_value))
        return Money(cents, charge.currency)
    elif split.split_type == "flat":
        # split_value is a Decimal, but it represents integer cents for
        # the flat case.  Round to int defensively.
        cents = _cents(Decimal(split.split_value))
        if cents > charge.cents:
            raise ValueError(
                f"Flat founder payout ({cents}{charge.currency}) exceeds "
                f"charge ({charge.cents}{charge.currency})"
            )
        return Money(cents, charge.currency)
    else:
        raise ValueError(f"Unknown split_type: {split.split_type!r}")


def validate_billing_plan_split(
    *,
    charge_amount: Money,
    usage_allocation: Money,
    payout_split: PayoutSplit,
) -> PlanValidationResult:
    """HARD VALIDATION — called at billing_plans.create()/update().

    Rules:
      founder_payout     = charge_amount * split_value (percentage)
                        OR split_value (flat)
      platform_remainder = charge_amount - founder_payout
      REJECT iff usage_allocation > platform_remainder
    """
    # 1. Currency consistency: everything must share charge_amount's
    #    currency.  (payout_split carries its own currency; we check
    #    that match inside compute_founder_payout.)
    if charge_amount.currency != usage_allocation.currency:
        return PlanValidationResult(
            ok=False,
            charge=charge_amount,
            founder_payout=Money(0, charge_amount.currency),
            platform_remainder=Money(0, charge_amount.currency),
            usage_allocation=usage_allocation,
            error_message=(
                f"Currency mismatch between charge ({charge_amount.currency}) "
                f"and usage_allocation ({usage_allocation.currency})"
            ),
        )

    try:
        founder = compute_founder_payout(charge_amount, payout_split)
    except ValueError as exc:
        return PlanValidationResult(
            ok=False,
            charge=charge_amount,
            founder_payout=Money(0, charge_amount.currency),
            platform_remainder=Money(0, charge_amount.currency),
            usage_allocation=usage_allocation,
            error_message=str(exc),
        )

    platform_remainder = charge_amount - founder
    # Can't have negative remainder — split math is bad.
    if platform_remainder.cents < 0:
        return PlanValidationResult(
            ok=False,
            charge=charge_amount,
            founder_payout=founder,
            platform_remainder=platform_remainder,
            usage_allocation=usage_allocation,
            error_message=(
                f"Founder payout ({founder}) exceeds charge ({charge_amount}). "
                f"Reduce split_value."
            ),
        )

    # THE rule the user specified.
    if usage_allocation > platform_remainder:
        shortfall = usage_allocation - platform_remainder
        return PlanValidationResult(
            ok=False,
            charge=charge_amount,
            founder_payout=founder,
            platform_remainder=platform_remainder,
            usage_allocation=usage_allocation,
            error_message=(
                f"Plan would OVER-COMMIT usage budget beyond what the "
                f"processor actually leaves SpendGuard.\n"
                f"  charge_amount         = {charge_amount}\n"
                f"  founder_payout        = {founder} "
                f"    (split={payout_split.split_type} {payout_split.split_value})\n"
                f"  platform_remainder    = {platform_remainder}\n"
                f"  requested allocation  = {usage_allocation}\n"
                f"  SHORTFALL             = {shortfall}\n"
                f"Fix it by: (a) raising charge_amount, "
                f"(b) lowering usage_allocation_cents, "
                f"or (c) reducing the founder's split_value on the payout_account."
            ),
        )

    return PlanValidationResult(
        ok=True,
        charge=charge_amount,
        founder_payout=founder,
        platform_remainder=platform_remainder,
        usage_allocation=usage_allocation,
    )


# ---------------------------------------------------------------------------
# Token-cost math (per-request, not checkout-time) — unchanged except for
# the new currency param defaulting to ETB.
# ---------------------------------------------------------------------------

_SG_PRICE_CARDS: dict[str, PriceCard] = {
    "inclusionai/ling-3.0-flash-vl:free": PriceCard(
        price_per_1k_input=Decimal("0.00000"),
        price_per_1k_output=Decimal("0.00000"),
        flat_fee_cents=Decimal("0.01"),
    ),
    "__default__": PriceCard(
        price_per_1k_input=Decimal("1.50"),
        price_per_1k_output=Decimal("6.00"),
        flat_fee_cents=Decimal("0.01"),
    ),
}


def compute_token_cost(
    price_card: PriceCard,
    input_tokens: int,
    output_tokens: int,
    currency: str = "ETB",
) -> TokenCost:
    """Pure function: price_card × token counts → TokenCost."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    input_cost = (Decimal(input_tokens) / Decimal("1000")) * price_card.price_per_1k_input
    output_cost = (Decimal(output_tokens) / Decimal("1000")) * price_card.price_per_1k_output
    return TokenCost(
        input_cost_cents=input_cost,
        output_cost_cents=output_cost,
        flat_fee_cents=price_card.flat_fee_cents,
        currency=currency,
    )


def sg_price_card_for(model: str) -> PriceCard:
    return _SG_PRICE_CARDS.get(model, _SG_PRICE_CARDS["__default__"])
