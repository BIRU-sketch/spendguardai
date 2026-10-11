-- =====================================================================
-- 002_microbilling_chapa.sql
-- SpendGuard AI microbilling tables — PROCESSOR-AGNOSTIC schema,
-- default processor = 'chapa' today.
--
-- DESIGN NOTE: No table or column is named stripe_* / chapa_* at the
-- schema level.  Instead a `processor TEXT` discriminator column tells
-- you *which* payment processor a row belongs to, so a second processor
-- (Stripe, PayPal, etc.) can plug in later without another schema
-- rewrite.  Per-processors fields live in JSONB / generic text columns.
--
-- IDEMPOTENCY: This file must run cleanly against ANY prior state,
-- including: (a) empty DB, (b) 001_microbilling.sql already applied
-- (where overlapping tables exist WITHOUT the 002-new columns), or
-- (c) this file itself already applied.  We therefore follow every
-- CREATE TABLE IF NOT EXISTS with a series of
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS for every non-PK column the
-- 002 schema declares, so a table left over from 001 gets the new
-- columns added (and if they already exist, it's a no-op).  Same for
-- CHECK constraints:  ALTER TABLE ... ADD CONSTRAINT ... IF NOT EXISTS.
-- =====================================================================

-- ---------------------------------------------------------------------
-- A) platform_settings — single-row table for business-level constants
--    that the founder/operator sets, NOT hardcoded.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform_settings (
    id                      SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- SpendGuard's cut of the platform_remainder, expressed as a
    -- decimal fraction, e.g. 0.10 = 10%.  The checkout math is:
    --     charge_amount
    --       − founder_payout    (Chapa subaccount split, per payout_accounts)
    --       = platform_remainder
    --         − usage_allocation_cents  (→ end_user grant balance)
    --         = platform_fee            (= platform_remainder × take_rate)
    -- Unset = take-rate not configured yet; checkout blocks.
    platform_fee_percent    NUMERIC(6, 5) NULL
        CHECK (platform_fee_percent IS NULL
            OR (platform_fee_percent >= 0 AND platform_fee_percent < 1)),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO platform_settings (id) VALUES (1)
    ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------
-- B) payout_accounts — one row per SpendGuard project (= developer).
--    Replaces connect_accounts; maps to Chapa Subaccounts in Chapa,
--    Stripe Connect Express accounts in Stripe, etc. — processor
--    agnostic; processor column tells you which one applies.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payout_accounts (
    project_id              TEXT PRIMARY KEY,
    processor               TEXT NOT NULL DEFAULT 'chapa',
    -- Processor's native account id:
    --   Chapa → subaccount id  (e.g. 'subacc_xxx' from Chapa API)
    --   Stripe → Connect account id (acct_xxx)
    processor_account_id    TEXT NOT NULL,
    -- Chapa-specific bank fields.  (Future Stripe processor will leave
    -- these NULL; the FKs and split logic only ever use processor +
    -- split_type + split_value, not the bank details.)
    bank_code               TEXT NOT NULL,
    account_number          TEXT NOT NULL,
    account_name            TEXT NOT NULL,
    -- Founder's cut — applied automatically by the processor on each
    -- transaction (Chapa's `subaccounts` list, Stripe's
    -- transfer_data.destination):
    --   percentage → founder gets charge_amount * split_value
    --   flat       → founder gets split_value (flat ETB cents)
    split_type              TEXT NOT NULL
        CHECK (split_type IN ('percentage', 'flat')),
    split_value             NUMERIC NOT NULL CHECK (split_value >= 0),
    -- pending → details submitted, waiting for processor-side KYC.
    -- complete → processor confirmed charges + payouts enabled.
    -- rejected → processor rejected onboarding.
    onboarding_state        TEXT NOT NULL DEFAULT 'pending'
        CHECK (onboarding_state IN ('pending', 'complete', 'rejected')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- C) billing_plans — subscription products a SpendGuard project sells
--    to THEIR customers via SpendGuard-hosted checkout.
--
--    CHANGE vs. Stripe draft: a plan now CARRIES ITS OWN charge amount
--    + currency.  Chapa has no Stripe-Price equivalent, so we can't
--    "sync" a plan to the processor first — the charge amount is set
--    at plan-creation time.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_plans (
    project_id              TEXT NOT NULL,
    plan_id                 TEXT NOT NULL,
    display_name            TEXT NOT NULL,
    description             TEXT,
    -- Processor-specific Price id if the processor has one (Stripe
    -- price_xxx); for Chapa this stays NULL — we send amount+currency
    -- at checkout-time instead of referencing a pre-registered Price.
    processor_price_id      TEXT NULL,
    -- ----------------  MONEY (amount charged to the end user) --------
    charge_amount_cents     BIGINT NOT NULL CHECK (charge_amount_cents >= 0),
    currency                CHAR(3)  NOT NULL DEFAULT 'ETB',
    -- ----------------  MONEY (end user AI grant balance) -------------
    usage_allocation_cents  BIGINT NOT NULL CHECK (usage_allocation_cents >= 0),
    -- Hard business validation (enforced at INSERT/UPDATE via app-code,
    -- also duplicated here as a DB-level CHECK belt-and-braces):
    --     usage_allocation_cents  ≤  charge_amount_cents
    -- plus  (after split calculation)
    --     usage_allocation_cents  ≤  platform_remainder
    -- DB can't do the split math without payout_accounts so only the
    -- first inequality lives here; the full check lives in Python.
    CONSTRAINT bp_charge_ge_alloc CHECK (usage_allocation_cents <= charge_amount_cents),
    expiry_days             INT NULL CHECK (expiry_days IS NULL OR expiry_days > 0),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order              INT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, plan_id)
);

-- Ensure every column exists even if billing_plans was created by 001
-- (which used stripe_* naming and had no currency / processor_price_id).
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS description             TEXT;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS processor_price_id      TEXT NULL;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS charge_amount_cents     BIGINT;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS currency                CHAR(3)  NOT NULL DEFAULT 'ETB';
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS usage_allocation_cents  BIGINT;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS expiry_days             INT;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS is_active               BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS sort_order              INT NOT NULL DEFAULT 0;
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE billing_plans ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW();
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.table_constraints
        WHERE  constraint_name = 'bp_charge_ge_alloc'
        AND    table_name      = 'billing_plans'
    ) THEN
        ALTER TABLE billing_plans
            ADD CONSTRAINT bp_charge_ge_alloc
            CHECK (usage_allocation_cents <= charge_amount_cents);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_billing_plans_active ON billing_plans(project_id, is_active);

-- ---------------------------------------------------------------------
-- D) usage_grants — SOURCE OF TRUTH for an end-user's spendable balance.
--    Processor-agnostic; funded by any completed checkout regardless
--    of which processor handled the payment.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_grants (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NOT NULL,
    plan_id                 TEXT NULL,
    source_reference        TEXT NULL,   -- tx_ref / checkout_sessions.session_id / sub id / operator ref
    source_type             TEXT NOT NULL DEFAULT 'checkout'
        CHECK (source_type IN ('checkout', 'subscription', 'topup', 'adjustment', 'refund')),
    -- ----------------  MONEY (currency-matched to the plan that funded it)
    granted_cents           BIGINT NOT NULL CHECK (granted_cents >= 0),
    remaining_cents         BIGINT NOT NULL CHECK (remaining_cents >= 0),
    currency                CHAR(3)  NOT NULL DEFAULT 'ETB',
    expires_at              TIMESTAMPTZ NULL,
    processor_charge_id     TEXT NULL,   -- processor's charge/txn id (denormalize for ops)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_last_at        TIMESTAMPTZ NULL,
    CHECK (remaining_cents <= granted_cents)
);

-- Add columns that are NEW in 002 vs. 001 (plan_id, source_reference,
-- source_type, currency, processor_charge_id, consumed_last_at,
-- expires_at) — 001's usage_grants was minimal, no currency, no source
-- tracking.
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS plan_id                 TEXT NULL;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS source_reference        TEXT NULL;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS source_type             TEXT NOT NULL DEFAULT 'checkout';
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS granted_cents           BIGINT;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS remaining_cents         BIGINT;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS currency                CHAR(3)  NOT NULL DEFAULT 'ETB';
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS expires_at              TIMESTAMPTZ NULL;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS processor_charge_id     TEXT NULL;
ALTER TABLE usage_grants ADD COLUMN IF NOT EXISTS consumed_last_at        TIMESTAMPTZ NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.table_constraints
        WHERE  constraint_name = 'ug_remaining_le_granted'
        AND    table_name      = 'usage_grants'
    ) THEN
        ALTER TABLE usage_grants
            ADD CONSTRAINT ug_remaining_le_granted
            CHECK (remaining_cents <= granted_cents);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_usage_grants_user_active ON usage_grants(
    project_id, end_user_id, currency, expires_at, created_at
) WHERE remaining_cents > 0;

-- ---------------------------------------------------------------------
-- E) billing_line_items — per-request cost journal (SpendGuard side +
--    end-user side).  Created BEFORE grant_consumptions because
--    grant_consumptions has a FK to it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_line_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NULL,
    ledger_event_id         TEXT NULL,
    model                   TEXT NOT NULL,
    input_tokens            INT NOT NULL,
    output_tokens           INT NOT NULL,
    total_tokens            INT NOT NULL,
    -- ----------------  MONEY ----------------------------------------
    sg_cost_cents           NUMERIC(12,6) NOT NULL,
    end_user_cost_cents     NUMERIC(12,6) NOT NULL,
    currency                CHAR(3)  NOT NULL DEFAULT 'ETB',
    pricing_tier            TEXT NOT NULL DEFAULT 'default',
    input_cost_cents        NUMERIC(12,6) NOT NULL DEFAULT 0,
    output_cost_cents       NUMERIC(12,6) NOT NULL DEFAULT 0,
    flat_fee_cost_cents     NUMERIC(12,6) NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'settled'
        CHECK (status IN ('pending', 'settled', 'refunded', 'void')),
    processor_charge_id     TEXT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 001's billing_line_items had no currency / pricing_tier / per-cost
-- breakdown / status / processor_charge_id — add them all IF NOT EXISTS.
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS project_id         TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE billing_line_items ALTER COLUMN project_id DROP DEFAULT;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS end_user_id        TEXT NULL;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS ledger_event_id    TEXT NULL;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS model              TEXT;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS input_tokens       INT;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS output_tokens      INT;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS total_tokens       INT;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS sg_cost_cents      NUMERIC(12,6);
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS end_user_cost_cents NUMERIC(12,6);
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS currency           CHAR(3)  NOT NULL DEFAULT 'ETB';
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS pricing_tier       TEXT NOT NULL DEFAULT 'default';
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS input_cost_cents   NUMERIC(12,6) NOT NULL DEFAULT 0;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS output_cost_cents  NUMERIC(12,6) NOT NULL DEFAULT 0;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS flat_fee_cost_cents NUMERIC(12,6) NOT NULL DEFAULT 0;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS status             TEXT NOT NULL DEFAULT 'settled';
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS processor_charge_id TEXT NULL;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW();
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.table_constraints
        WHERE  constraint_name = 'bli_status_enum'
        AND    table_name      = 'billing_line_items'
    ) THEN
        ALTER TABLE billing_line_items
            ADD CONSTRAINT bli_status_enum
            CHECK (status IN ('pending', 'settled', 'refunded', 'void'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_bli_project_user ON billing_line_items(
    project_id, end_user_id, created_at DESC
) WHERE end_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bli_ledger ON billing_line_items(ledger_event_id)
    WHERE ledger_event_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- F) grant_consumptions — per-request audit trail of FIFO draws.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grant_consumptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grant_id                UUID NOT NULL REFERENCES usage_grants(id) ON DELETE CASCADE,
    billing_line_item_id    UUID NOT NULL REFERENCES billing_line_items(id) ON DELETE CASCADE,
    cents_drawn             BIGINT NOT NULL CHECK (cents_drawn > 0),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure FK + data columns exist even if 001 built a minimal version
ALTER TABLE grant_consumptions ADD COLUMN IF NOT EXISTS grant_id             UUID;
ALTER TABLE grant_consumptions ADD COLUMN IF NOT EXISTS billing_line_item_id UUID;
ALTER TABLE grant_consumptions ADD COLUMN IF NOT EXISTS cents_drawn          BIGINT;
ALTER TABLE grant_consumptions ADD COLUMN IF NOT EXISTS created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW();
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.table_constraints
        WHERE  constraint_name = 'gc_cents_positive'
        AND    table_name      = 'grant_consumptions'
    ) THEN
        ALTER TABLE grant_consumptions
            ADD CONSTRAINT gc_cents_positive
            CHECK (cents_drawn > 0);
    END IF;
END $$;
-- Note: FKs use REFERENCES ... ON DELETE CASCADE; we do NOT force them
-- via ALTER TABLE IF NOT EXISTS because (a) if they already exist it's
-- fine, and (b) a FK with a different name pointing to the same pair
-- of columns is semantically equivalent.  If the columns are present,
-- the FK constraint already exists from the 001 or 002 CREATE TABLE.

CREATE INDEX IF NOT EXISTS idx_grant_consumptions_grant ON grant_consumptions(grant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_grant_consumptions_bli   ON grant_consumptions(billing_line_item_id);

-- ---------------------------------------------------------------------
-- G) payment_events — idempotency journal for webhook deliveries from
--    ANY processor.  stripe_events is replaced wholesale.
--    (This table has a DIFFERENT name from 001's stripe_events, so the
--    CREATE TABLE IF NOT EXISTS will never be a no-op on a 001 DB —
--    the new name is the idempotency key.)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_events (
    -- Processor's native event id (Chapa has no canonical event id
    -- format, so we compute: sha256(processor + ':' + raw_payload)
    -- when one isn't present; see payment_events.record()).
    processor_event_id      TEXT PRIMARY KEY,
    processor               TEXT NOT NULL DEFAULT 'chapa',
    event_type              TEXT NOT NULL,      -- checkout.completed / account.updated / charge.succeeded ...
    processor_account_id    TEXT NULL,          -- Connect acct / Chapa subacc if applicable
    raw_payload             JSONB NOT NULL,
    received_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered               BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at            TIMESTAMPTZ NULL,
    last_error              TEXT NULL
);

-- ---------------------------------------------------------------------
-- H) checkout_sessions — lightweight journal of every checkout we
--    initiated.  tx_ref is what Chapa calls it; session_id is kept as
--    the generic column name (still the primary key).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkout_sessions (
    session_id              TEXT PRIMARY KEY,       -- our generated tx_ref / cs_xxx
    processor               TEXT NOT NULL DEFAULT 'chapa',
    project_id              TEXT NOT NULL,
    end_user_id             TEXT NULL,
    plan_id                 TEXT NOT NULL,
    processor_account_id    TEXT NOT NULL,          -- subaccount / acct_xxx to split to
    -- ----------------  MONEY ----------------------------------------
    charge_amount_cents     BIGINT NOT NULL,
    currency                CHAR(3)  NOT NULL DEFAULT 'ETB',
    status                  TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'completed', 'expired', 'cancelled')),
    actual_charge_cents     BIGINT NULL,
    usage_grant_id          UUID NULL REFERENCES usage_grants(id),
    processor_checkout_url  TEXT NULL,              -- Chapa checkout_url (repeatable, no refresh needed)
    processor_txn_ref       TEXT NULL,              -- Chapa tx_ref if different from session_id
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 001's checkout_sessions was stripe-shaped; add every 002-new column
-- (processor, end_user_id, processor_account_id, currency,
-- actual_charge_cents, usage_grant_id, processor_checkout_url,
-- processor_txn_ref, updated_at, status enum) IF NOT EXISTS.
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS processor             TEXT NOT NULL DEFAULT 'chapa';
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS project_id            TEXT;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS end_user_id           TEXT NULL;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS plan_id               TEXT;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS processor_account_id  TEXT;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS charge_amount_cents   BIGINT;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS currency              CHAR(3)  NOT NULL DEFAULT 'ETB';
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS status                TEXT NOT NULL DEFAULT 'open';
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS actual_charge_cents   BIGINT NULL;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS usage_grant_id        UUID NULL;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS processor_checkout_url TEXT NULL;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS processor_txn_ref     TEXT NULL;
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE checkout_sessions ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW();
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'checkout_sessions' AND column_name = 'connect_account_id'
    ) THEN
        ALTER TABLE checkout_sessions ALTER COLUMN connect_account_id DROP NOT NULL;
    END IF;
END $$;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.table_constraints
        WHERE  constraint_name = 'cs_status_enum'
        AND    table_name      = 'checkout_sessions'
    ) THEN
        ALTER TABLE checkout_sessions
            ADD CONSTRAINT cs_status_enum
            CHECK (status IN ('open', 'completed', 'expired', 'cancelled'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_cs_project ON checkout_sessions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_txn_ref ON checkout_sessions(processor_txn_ref)
    WHERE processor_txn_ref IS NOT NULL;

-- =====================================================================
-- end_users dim table (unchanged shape — kept as-is but idempotent)
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

ALTER TABLE end_users ADD COLUMN IF NOT EXISTS display_name    TEXT;
ALTER TABLE end_users ADD COLUMN IF NOT EXISTS metadata        JSONB DEFAULT '{}'::jsonb;
ALTER TABLE end_users ADD COLUMN IF NOT EXISTS first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE end_users ADD COLUMN IF NOT EXISTS last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_end_users_project ON end_users(project_id);
