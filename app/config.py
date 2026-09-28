"""
Runtime configuration — loads environment variables once at startup and
re-exposes them as typed, validated values. Nothing outside this module
reads os.environ directly; everything imports from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ChapaConfig:
    """Everything the Chapa client needs.  All secrets are optional at
    import time (so the app still boots for lint/test without Chapa
    creds); payment_processor.py validates them on first real use and
    raises a clear error if they're missing.

    Chapa docs: https://developer.chapa.co/docs/overview/
    Test keys live at https://dashboard.chapa.co/settings/api-keys — they
    start with `CHASECK_TEST-` (secret) and `CHAPUBK_TEST-` (public).
    """

    # Secret key — starts with CHASECK_TEST- (test) or CHASECK_LIVE- (live).
    # Used as Authorization: Bearer <secret_key> for all API calls.
    secret_key: str | None

    # Public key — CHAPUBK_TEST-xxx or CHAPUBK_LIVE-xxx.  Not used
    # server-side, but forwarded to the frontend for Chapa.js Checkout
    # if we ever embed instead of redirect.
    public_key: str | None

    # Chapa's webhook hash secret.  You set this in Dashboard →
    # Settings → Webhooks → "Webhook HMAC Secret" (or the dashboard
    # auto-generates one for you when you add a webhook).  If unset,
    # webhook payloads are NOT verified in non-prod — NEVER ship to
    # prod without it.
    webhook_hmac_secret: str | None

    # Human-readable platform name — shown in Chapa's email/SMS
    # receipts, the hosted checkout page, etc.
    platform_display_name: str

    # Which processor is ACTIVE.  Today it's hardcoded 'chapa'; a
    # future multi-processor rollout can read this from
    # platform_settings or from project-level config instead of an env
    # var.  Callers that need a processor discriminator should prefer
    # this singleton rather than sprinkling `'chapa'` literals through
    # the codebase.
    processor: str


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    redis_url: str
    upstream_base_url: str
    upstream_base_key: str
    supabase_url: str
    supabase_key: str
    chapa: ChapaConfig

    @property
    def public_base_url(self) -> str:
        """Public base URL the app is reachable at — used for Chapa
        callback_url (webhook endpoint), success_url, and cancel_url
        construction.  Read from env or fall back to localhost for
        local dev."""
        return os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")


def _chapa() -> ChapaConfig:
    return ChapaConfig(
        secret_key=os.getenv("CHAPA_SECRET_KEY"),
        public_key=os.getenv("CHAPA_PUBLIC_KEY"),
        webhook_hmac_secret=os.getenv("CHAPA_WEBHOOK_HMAC_SECRET"),
        platform_display_name=os.getenv("CHAPA_PLATFORM_NAME", "SpendGuard AI"),
        processor=os.getenv("PAYMENT_PROCESSOR", "chapa").strip().lower() or "chapa",
    )


def load() -> AppConfig:
    """Called once at app startup; re-imports just return the singleton
    via the module-level variable below."""
    return AppConfig(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        upstream_base_url=os.environ["UPSTREAM_BASE_URL"],
        upstream_base_key=os.environ["UPSTREAM_BASE_KEY"],
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_KEY"],
        chapa=_chapa(),
    )


# Singleton — spendguard boots once, config doesn't change mid-process.
# Access via `from app.config import config` anywhere after startup.
config: AppConfig = load()


# ---------------------------------------------------------------------------
# Helpers for the PLATFORM_FEE_PERCENT business constant.
#
# Rule: this number comes from the DB (platform_settings.platform_fee_percent)
# at runtime — NOT from an env var and NOT from a hardcoded default. The
# env var here is ONLY an optional bootstrap path for local development so
# the founder doesn't need to manually UPDATE a table row while testing.
# ---------------------------------------------------------------------------

PLATFORM_FEE_ENV_FALLBACK: Decimal | None = None
_raw = os.getenv("PLATFORM_FEE_PERCENT")
if _raw is not None and _raw.strip() != "":
    try:
        PLATFORM_FEE_ENV_FALLBACK = Decimal(_raw)
    except Exception:
        # Don't crash startup — let the DB value win, and if that's also
        # missing the charge creation path raises a clear error.
        PLATFORM_FEE_ENV_FALLBACK = None
