"""
Pricing engine — token cost calculations and model pricing cards.
Standardized on USD cents (1 USD = 100 cents).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


# ---------------------------------------------------------------------------
# Money — currency-aware integer cents. Currency is ISO 4217 CHAR(3),
# uppercase-normalized on construction.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Money:
    """Immutable (cents, currency) pair."""

    cents: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError(f"cents must be an int, got {type(self.cents).__name__}")
        normalized_currency = (self.currency or "USD").strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isalpha():
            raise ValueError(
                f"currency must be a 3-letter ISO code, got {self.currency!r}"
            )
        object.__setattr__(self, "currency", normalized_currency)

    def _require_same_currency(self, other: "Money", op: str) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot {op} Money in different currencies: "
                f"{self.currency} vs {other.currency}"
            )

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other, "+")
        return Money(cents=self.cents + other.cents, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._require_same_currency(other, "-")
        return Money(cents=self.cents - other.cents, currency=self.currency)

    def __mul__(self, factor: Decimal | int) -> "Money":
        if isinstance(factor, int):
            return Money(cents=self.cents * factor, currency=self.currency)
        scaled = (Decimal(self.cents) * factor).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return Money(cents=int(scaled), currency=self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._require_same_currency(other, "<")
        return self.cents < other.cents

    def __le__(self, other: "Money") -> bool:
        self._require_same_currency(other, "<=")
        return self.cents <= other.cents

    def __gt__(self, other: "Money") -> bool:
        self._require_same_currency(other, ">")
        return self.cents > other.cents

    def __ge__(self, other: "Money") -> bool:
        self._require_same_currency(other, ">=")
        return self.cents >= other.cents

    def __neg__(self) -> "Money":
        return Money(cents=-self.cents, currency=self.currency)

    def is_zero(self) -> bool:
        return self.cents == 0

    def __str__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        abs_cents = abs(self.cents)
        return f"{sign}{self.currency} {abs_cents // 100}.{abs_cents % 100:02d}"


def sum_money(items: list[Money], expected_currency: str = "USD") -> Money:
    """Sum a list of Money items, enforcing currency homogeneity."""
    if not items:
        return Money(0, expected_currency)
    curr = items[0].currency
    total = 0
    for item in items:
        if item.currency != curr:
            raise ValueError(
                f"Mixed currencies in sum_money: expected {curr}, got {item.currency}"
            )
        total += item.cents
    return Money(cents=total, currency=curr)


# ---------------------------------------------------------------------------
# Per-request token-cost math
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenCost:
    """Cost breakdown for a single completion request."""

    input_cost_cents: Decimal
    output_cost_cents: Decimal
    flat_fee_cents: Decimal
    currency: str = "USD"

    @property
    def total_cents(self) -> Decimal:
        return self.input_cost_cents + self.output_cost_cents + self.flat_fee_cents

    def as_money(self) -> Money:
        int_cents = int(self.total_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return Money(cents=int_cents, currency=self.currency)


@dataclass(frozen=True)
class PriceCard:
    """Pricing card per 1,000 tokens in cents."""

    price_per_1k_input: Decimal
    price_per_1k_output: Decimal
    flat_fee_cents: Decimal = Decimal(0)


# Default model prices in USD cents per 1k tokens
_SG_PRICE_CARDS: dict[str, PriceCard] = {
    # OpenAI
    "gpt-4o": PriceCard(price_per_1k_input=Decimal("0.25"), price_per_1k_output=Decimal("1.00")),
    "gpt-4o-mini": PriceCard(price_per_1k_input=Decimal("0.015"), price_per_1k_output=Decimal("0.060")),
    "o1": PriceCard(price_per_1k_input=Decimal("1.50"), price_per_1k_output=Decimal("6.00")),
    "o1-mini": PriceCard(price_per_1k_input=Decimal("0.11"), price_per_1k_output=Decimal("0.44")),
    "o3-mini": PriceCard(price_per_1k_input=Decimal("0.11"), price_per_1k_output=Decimal("0.44")),
    "gpt-4-turbo": PriceCard(price_per_1k_input=Decimal("1.00"), price_per_1k_output=Decimal("3.00")),
    # Anthropic
    "claude-3-7-sonnet": PriceCard(price_per_1k_input=Decimal("0.30"), price_per_1k_output=Decimal("1.50")),
    "claude-3-5-sonnet": PriceCard(price_per_1k_input=Decimal("0.30"), price_per_1k_output=Decimal("1.50")),
    "claude-3-5-haiku": PriceCard(price_per_1k_input=Decimal("0.08"), price_per_1k_output=Decimal("0.40")),
    "claude-3-opus": PriceCard(price_per_1k_input=Decimal("1.50"), price_per_1k_output=Decimal("7.50")),
    # Google Gemini
    "gemini-2.0-flash": PriceCard(price_per_1k_input=Decimal("0.01"), price_per_1k_output=Decimal("0.04")),
    "gemini-1.5-pro": PriceCard(price_per_1k_input=Decimal("0.125"), price_per_1k_output=Decimal("0.50")),
    "gemini-1.5-flash": PriceCard(price_per_1k_input=Decimal("0.0075"), price_per_1k_output=Decimal("0.03")),
    # DeepSeek
    "deepseek-chat": PriceCard(price_per_1k_input=Decimal("0.014"), price_per_1k_output=Decimal("0.028")),
    "deepseek-reasoner": PriceCard(price_per_1k_input=Decimal("0.055"), price_per_1k_output=Decimal("0.219")),
    # Groq / Llama
    "llama-3.3-70b-versatile": PriceCard(price_per_1k_input=Decimal("0.059"), price_per_1k_output=Decimal("0.079")),
    "llama-3.1-8b-instant": PriceCard(price_per_1k_input=Decimal("0.005"), price_per_1k_output=Decimal("0.008")),
    # Default fallback
    "__default__": PriceCard(price_per_1k_input=Decimal("0.15"), price_per_1k_output=Decimal("0.60")),
}


def compute_token_cost(
    price_card: PriceCard,
    input_tokens: int,
    output_tokens: int,
    currency: str = "USD",
) -> TokenCost:
    """Computes TokenCost from token counts and price card."""
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
    clean = model.lower().split("/")[-1]
    if clean in _SG_PRICE_CARDS:
        return _SG_PRICE_CARDS[clean]
    for key, card in _SG_PRICE_CARDS.items():
        if key != "__default__" and clean.startswith(key):
            return card
    return _SG_PRICE_CARDS["__default__"]


_UPSTREAM_PRICE_CARDS: dict[str, PriceCard] = {
    "__default__": PriceCard(
        price_per_1k_input=Decimal("0.15"),
        price_per_1k_output=Decimal("0.60"),
        flat_fee_cents=Decimal("0.00"),
    ),
}


def upstream_price_card_for(model: str) -> PriceCard:
    return _UPSTREAM_PRICE_CARDS.get(model, _UPSTREAM_PRICE_CARDS["__default__"])
