-- =====================================================================
-- 001_microbilling.sql
-- SpendGuard AI microbilling tables (Stripe Connect platform).
-- Apply once; idempotent via IF NOT EXISTS where possible.
-- =====================================================================

-- ---------------------------------------------------------------------
-- A) platform_settings — single-row table for business-level constants
--    that the founder/operator sets, NOT hardcoded.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform_settings (
    id                      SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- SpendGuard's cut on every destination charge that moves through
    -- Connect microbilling. Expressed as a decimal fraction, e.g.
    -- 0.10 = 10%. Unset = take-rate not configured yet; checkout blocks.
    -- This is the ONLY place this number lives; never mirror it in code.
    platform_fee_percent    NUMERIC(6, 5) NULL
        CHECK (platform_fee_percent IS NULL
            OR (platform_fee_percent >= 0 AND platform_fee_percent < 1)),
    -- Stripe IDs for the platform itself (set once at provisioning)
    stripe_platform_account_id  TEXT NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the singleton row on first deploy. The founder fills in the
-- actual take-rate (and Stripe account ID) via the operator dashboard
-- or via the operator CLI — NOT by editing this file.
INSERT INTO platform_settings (id) VALUES (1)
    ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------
-- B) connect_accounts — one row per SpendGuard project (= developer).
--    Stores the Stripe Connect Express account the developer onboarded
--    into; every destination charge uses account -> stripe_account_id.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS connect_accounts (
    project_id              TEXT PRIMARY KEY,
    -- Stripe Connect account id (acct_xxx) for the developer — this is
    -- the destination on every charge.
    stripe_account_id       TEXT NOT NULL,
    -- "restricted" (onboarding started, details submitted, under review)
    -- or "complete" (charges_enabled + payouts_enabled in Stripe).
    -- Only "complete" accounts can run checkout.
    onboarding_state        TEXT NOT NULL DEFAULT 'restricted'
        CHECK (onboarding_state IN ('restricted', 'complete', 'rejected')),
    -- Link returned by Stripe AccountLink.create — consumed once.
    onboarding_link_url     TEXT NULL,
    onboarding_link_expires TIMESTAMPTZ NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- C) billing_plans — subscription products a SpendGuard project (= the
--    developer) sells to THEIR customers through the SpendGuard-hosted
--    checkout flow.
--    One project has many plans; each plan maps 1:1 to a Stripe Price
--    (price_xxx) created on the Connect account via the API.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_plans (
    project_id              TEXT NOT NULL,
    plan_id                 TEXT NOT NULL,           -- e.g. "pro_100k_monthly"
    -- Human-readable — shown on Stripe-hosted checkout
    display_name            TEXT NOT NULL,
    description             TEXT,
    -- Stripe Price id (price_xxx) on the Connect account. NULL until
    -- the project admin actually syncs the plan to Stripe.
    stripe_price_id         TEXT NULL,
    -- ---------------------------------------------------------------
    -- The three-way split lives HERE, per plan:
    --
    --   charge_amount (from Stripe Price)
    --     ├─ usage_allocation_cents   → end_user's AI usage balance
    --     ├─ platform_fee             → SpendGuard (auto via application_fee_amount)
    --     └─ founder_payout           → developer (remainder, auto via transfer_data)
    --
    -- usage_allocation_cents is the ONLY number the founder/developer
    -- sets. Both the platform fee AND founder payout are derived
    -- automatically from charge_amount at checkout time — the plan
    -- table does NOT store either of them, because the take-rate is a
    -- platform-wide setting, not a per-plan setting.
    -- ---------------------------------------------------------------
    usage_allocation_cents  BIGINT NOT NULL CHECK (usage_allocation_cents >= 0),
    -- Plan expiry relative to checkout. The usage_grants row from
    -- this plan expires at (checkout_timestamp + expiry_days). NULL
    -- means the grant never expires on its own (still consumed on use).
    expiry_days             INT NULL CHECK (expiry_days IS NULL OR expiry_days > 0),
    -- Soft-delete / off-sale. Archived plans still redeem existing
    -- grants; only new checkout is blocked.
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order              INT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, plan_id)
);
CREATE INDEX IF NOT EXISTS idx_billing_plans_active ON billing_plans(project_id, is_active);

-- ---------------------------------------------------------------------
-- D) usage_grants — the SOURCE OF TRUTH for an end-user's spendable AI
--    balance.
--
--    Every top-up, every subscription checkout, every manual credit
--    adjustment writes a row HERE. The end-user's current balance is
--    the SUM(remaining_cents) across all non-expired, non-exhausted
--    grants for their (project_id, end_user_id) pair.
--
--    Consumption on every gateway request is FIFO across the user's
--    active grants (oldest expires-first — matches typical SaaS
--    expectations and matches the grant-level expiry we track).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_grants (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NOT NULL,
    plan_id                 TEXT NULL,             -- NULL = manual credit/adjustment
    -- The Stripe Checkout Session id (cs_test_xxx) or subscription id
    -- (sub_xxx) or operator reference that caused this grant to exist.
    source_reference        TEXT NULL,
    source_type             TEXT NOT NULL DEFAULT 'checkout'
        CHECK (source_type IN ('checkout', 'subscription', 'topup', 'adjustment', 'refund')),
    -- Original cents deposited by this grant (never mutates — for
    -- audit/history).
    granted_cents           BIGINT NOT NULL CHECK (granted_cents >= 0),
    -- Cents still usable. Starts == granted_cents; decremented FIFO
    -- on each request; never goes below zero.
    remaining_cents         BIGINT NOT NULL CHECK (remaining_cents >= 0),
    -- Hard boundary on this individual grant's validity window.
    expires_at              TIMESTAMPTZ NULL,
    -- Redundant but convenient: denormalize the charge_id so we can
    -- answer "which purchases funded this user?" without 4 joins.
    stripe_charge_id        TEXT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_last_at        TIMESTAMPTZ NULL,
    CHECK (remaining_cents <= granted_cents)
);
CREATE INDEX IF NOT EXISTS idx_usage_grants_user_active ON usage_grants(
    project_id, end_user_id, expires_at, created_at
) WHERE remaining_cents > 0;

-- ---------------------------------------------------------------------
-- E) billing_line_items (previously sketched — now real, with Stripe
--    charge lineage back to the grant(s) that funded it).
--    Re-declared here because the ledger_events table already exists
--    per app/ledger.py; we just formalize this journal alongside it.
--
--    NOTE: created BEFORE grant_consumptions because grant_consumptions
--    has a FK REFERENCES billing_line_items(id).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_line_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NULL,
    ledger_event_id         TEXT NULL,             -- ledger_events.event_id (UUID stored as TEXT)
    model                   TEXT NOT NULL,
    input_tokens            INT NOT NULL,
    output_tokens           INT NOT NULL,
    total_tokens            INT NOT NULL,
    -- What *SpendGuard* charges the developer for this request
    sg_cost_cents           NUMERIC(12,6) NOT NULL,
    -- What the *end-user* was billed — drawn from their grants FIFO
    end_user_cost_cents     NUMERIC(12,6) NOT NULL,
    pricing_tier            TEXT NOT NULL DEFAULT 'default',
    input_cost_cents        NUMERIC(12,6) NOT NULL DEFAULT 0,
    output_cost_cents       NUMERIC(12,6) NOT NULL DEFAULT 0,
    flat_fee_cost_cents     NUMERIC(12,6) NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'settled'
        CHECK (status IN ('pending', 'settled', 'refunded', 'void')),
    -- Stripe lineage: which specific funded this draw (redundant with
    -- grant_consumptions join, handy for operator queries).
    stripe_charge_id        TEXT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bli_project_user ON billing_line_items(
    project_id, end_user_id, created_at DESC
) WHERE end_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bli_ledger ON billing_line_items(ledger_event_id)
    WHERE ledger_event_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- F) grant_consumptions — per-request audit trail of FIFO draws.
--    One billing_line_item can draw zero or more grants (a large single
--    request may exhaust the oldest grant and dip into the next one).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grant_consumptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grant_id                UUID NOT NULL REFERENCES usage_grants(id) ON DELETE CASCADE,
    billing_line_item_id    UUID NOT NULL REFERENCES billing_line_items(id) ON DELETE CASCADE,
    cents_drawn             BIGINT NOT NULL CHECK (cents_drawn > 0),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_grant_consumptions_grant ON grant_consumptions(grant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_grant_consumptions_bli   ON grant_consumptions(billing_line_item_id);

-- ---------------------------------------------------------------------
-- G) stripe_events — idempotency + replay journal for webhooks.
--    We write every event here BEFORE processing; process() only runs
--    if delivered=0. Guarantees at-least-once delivery from Stripe
--    never causes double-granting.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stripe_events (
    stripe_event_id         TEXT PRIMARY KEY,       -- evt_xxx (real Stripe id)
    event_type              TEXT NOT NULL,
    stripe_account_id       TEXT NULL,              -- acct_xxx on Connect events
    raw_payload             JSONB NOT NULL,
    received_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered               BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at            TIMESTAMPTZ NULL,
    last_error              TEXT NULL
);

-- ---------------------------------------------------------------------
-- H) checkout_sessions — lightweight journal of every checkout we
--    initiated. Lets us:
--      1) on checkout.session.completed, look up (project, plan, user)
--         from the metadata we stashed in the Session
--      2) prevent double-crediting if the webhook fires twice (we
--         already have stripe_events, but this is operator-friendly)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkout_sessions (
    session_id              TEXT PRIMARY KEY,       -- cs_xxx from Stripe
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NULL,
    plan_id                 TEXT NOT NULL,
    connect_account_id      TEXT NOT NULL,          -- acct_xxx
    charge_amount_cents     BIGINT NOT NULL,        -- the amount we *thought* we'd charge
    status                  TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'completed', 'expired', 'cancelled')),
    -- Populated on the webhook. If status='completed', these are the
    -- REAL numbers from Stripe (not our pre-checkout estimate):
    actual_charge_cents     BIGINT NULL,
    usage_grant_id          UUID NULL REFERENCES usage_grants(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cs_project ON checkout_sessions(project_id, created_at DESC);

-- =====================================================================
-- Convenience: end_users dim table (we upsert on first sighting, so
-- operator dashboards can show display_name instead of the opaque id).
-- =====================================================================
CREATE TABLE IF NOT EXISTS end_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      TEXT NOT NULL,
    end_user_id     TEXT NOT NULL,
    display_name    TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, end_user_id)
);
CREATE INDEX IF NOT EXISTS idx_end_users_project ON end_users(project_id);
