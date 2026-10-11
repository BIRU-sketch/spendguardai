-- =====================================================================
-- 005_hybrid_monetization.sql
-- SpendGuard AI Hybrid Monetization Engine
-- Developer Subscriptions, Wallets, Exposure Caps, and Financial Ledger
-- =====================================================================

-- ---------------------------------------------------------------------
-- A) developer_wallets — real-money balance per developer/project
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS developer_wallets (
    project_id                  TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    developer_id                TEXT NULL,
    balance_cents               BIGINT NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    developer_revenue_cents     BIGINT NOT NULL DEFAULT 0 CHECK (developer_revenue_cents >= 0),
    platform_revenue_cents      BIGINT NOT NULL DEFAULT 0 CHECK (platform_revenue_cents >= 0),
    exposure_cap_cents          BIGINT NULL CHECK (exposure_cap_cents IS NULL OR exposure_cap_cents >= 0),
    exposure_used_cents         BIGINT NOT NULL DEFAULT 0 CHECK (exposure_used_cents >= 0),
    developer_pays_upstream_cost BOOLEAN NOT NULL DEFAULT FALSE,
    currency                    CHAR(3) NOT NULL DEFAULT 'ETB',
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS developer_id TEXT NULL;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS balance_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS developer_revenue_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS platform_revenue_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS exposure_cap_cents BIGINT NULL;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS exposure_used_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS developer_pays_upstream_cost BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS currency CHAR(3) NOT NULL DEFAULT 'ETB';
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_developer_wallets_dev_id ON developer_wallets(developer_id) WHERE developer_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- B) wallet_ledger — immutable financial audit log for all movements
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wallet_ledger (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                  TEXT NOT NULL,
    developer_id                TEXT NULL,
    end_user_id                 TEXT NULL,
    entry_type                  TEXT NOT NULL
        CHECK (entry_type IN ('credit', 'debit', 'refund', 'reversal', 'subscription_purchase', 'usage_deduction')),
    wallet_delta_cents          BIGINT NOT NULL DEFAULT 0,
    developer_share_delta_cents BIGINT NOT NULL DEFAULT 0,
    platform_revenue_delta_cents BIGINT NOT NULL DEFAULT 0,
    upstream_provider_cost_cents BIGINT NOT NULL DEFAULT 0,
    balance_after_cents         BIGINT NOT NULL,
    currency                    CHAR(3) NOT NULL DEFAULT 'ETB',
    reference_id                TEXT NULL,
    description                 TEXT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wallet_ledger_project ON wallet_ledger(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_dev ON wallet_ledger(developer_id, created_at DESC) WHERE developer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(project_id, end_user_id, created_at DESC) WHERE end_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_ref ON wallet_ledger(reference_id) WHERE reference_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- C) developer_subscription_plans — pre-configured plans for paid tiers
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS developer_subscription_plans (
    plan_id                     TEXT PRIMARY KEY,
    tier                        TEXT NOT NULL
        CHECK (tier IN ('builder', 'team', 'enterprise')),
    display_name                TEXT NOT NULL,
    description                 TEXT NULL,
    charge_amount_cents         BIGINT NOT NULL CHECK (charge_amount_cents > 0),
    currency                    CHAR(3) NOT NULL DEFAULT 'ETB',
    wallet_allocation_cents     BIGINT NOT NULL CHECK (wallet_allocation_cents >= 0),
    developer_share_cents       BIGINT NOT NULL CHECK (developer_share_cents >= 0),
    platform_fee_cents          BIGINT NOT NULL CHECK (platform_fee_cents >= 0),
    developer_pays_upstream_cost BOOLEAN NOT NULL DEFAULT FALSE,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order                  INT NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT dsp_split_sum CHECK (wallet_allocation_cents + developer_share_cents + platform_fee_cents = charge_amount_cents)
);

-- Seed standard developer subscription plans
INSERT INTO developer_subscription_plans
    (plan_id, tier, display_name, description, charge_amount_cents, currency,
     wallet_allocation_cents, developer_share_cents, platform_fee_cents, developer_pays_upstream_cost, sort_order)
VALUES
    ('builder_monthly', 'builder', 'Builder Monthly', 'Hybrid AI wallet + revenue share for builders',
     500000, 'ETB', 350000, 100000, 50000, false, 1),
    ('team_monthly', 'team', 'Team Scale', 'High-throughput wallet with developer upstream cost coverage',
     2000000, 'ETB', 1500000, 340000, 160000, true, 2),
    ('enterprise_scale', 'enterprise', 'Enterprise Custom', 'Maximum exposure cap with optimal platform fee',
     10000000, 'ETB', 8000000, 1500000, 500000, true, 3)
ON CONFLICT (plan_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- D) developer_subscriptions — active developer subscription records
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS developer_subscriptions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id                  TEXT NOT NULL UNIQUE,
    project_id                  TEXT NOT NULL,
    developer_id                TEXT NULL,
    plan_id                     TEXT NOT NULL REFERENCES developer_subscription_plans(plan_id),
    status                      TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'pending', 'canceled', 'expired')),
    charge_amount_cents         BIGINT NOT NULL,
    wallet_allocation_cents     BIGINT NOT NULL,
    developer_share_cents       BIGINT NOT NULL,
    platform_revenue_cents      BIGINT NOT NULL,
    currency                    CHAR(3) NOT NULL DEFAULT 'ETB',
    processor                   TEXT NOT NULL DEFAULT 'chapa',
    processor_txn_ref           TEXT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dev_subs_project ON developer_subscriptions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_dev_subs_session ON developer_subscriptions(session_id);
