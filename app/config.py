"""
Runtime configuration — loads environment variables once at startup and
re-exposes them as typed, validated values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    redis_url: str
    upstream_base_url: str
    upstream_base_key: str
    supabase_url: str
    supabase_key: str
    admin_api_key: str | None = None
    default_currency: str = "USD"
    default_run_budget_cents: int = 100  # Default $1.00 hard spend limit per agent run
    default_max_steps_per_run: int = 50   # Default runaway loop step threshold
    reservation_ttl_seconds: int = 120    # 2 minutes default before abandoned reservation is reaped
    reaper_interval_seconds: float = 5.0  # Reaper sweep interval
    public_base_url: str = "http://localhost:8000"


def load() -> AppConfig:
    """Loads configuration from environment variables."""
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", ""),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/"),
        upstream_base_url=os.environ.get("UPSTREAM_BASE_URL", "https://api.openai.com/v1"),
        upstream_base_key=os.environ.get("UPSTREAM_BASE_KEY", ""),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_KEY", ""),
        admin_api_key=os.getenv("ADMIN_API_KEY") or os.getenv("SPENDGUARD_ADMIN_KEY"),
        default_currency=os.getenv("DEFAULT_CURRENCY", "USD"),
        default_run_budget_cents=int(os.getenv("DEFAULT_RUN_BUDGET_CENTS", "100")),
        default_max_steps_per_run=int(os.getenv("DEFAULT_MAX_STEPS_PER_RUN", "50")),
        reservation_ttl_seconds=int(os.getenv("RESERVATION_TTL_SECONDS", "120")),
        reaper_interval_seconds=float(os.getenv("REAPER_INTERVAL_SECONDS", "5.0")),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"),
    )


# Singleton instance
config: AppConfig = load()
