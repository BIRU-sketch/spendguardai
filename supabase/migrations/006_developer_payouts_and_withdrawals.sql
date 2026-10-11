-- =====================================================================
-- 006_developer_payouts_and_withdrawals.sql
-- Developer Payout Onboarding, Wallet Balances, and Withdrawal Records
-- =====================================================================

-- ---------------------------------------------------------------------
-- A) developer_wallets — enhanced balances (available, reserved, pending)
-- ---------------------------------------------------------------------
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS available_balance_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS reserved_balance_cents BIGINT NOT NULL DEFAULT 0;
ALTER TABLE developer_wallets ADD COLUMN IF NOT EXISTS pending_withdrawal_cents BIGINT NOT NULL DEFAULT 0;

-- Sync available_balance_cents to match balance_cents if available is 0
UPDATE developer_wallets
SET available_balance_cents = balance_cents
WHERE available_balance_cents = 0 AND balance_cents > 0;

-- ---------------------------------------------------------------------
-- B) wallet_ledger — update check constraint for all required entry types
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'wallet_ledger_entry_type_check' AND table_name = 'wallet_ledger'
    ) THEN
        ALTER TABLE wallet_ledger DROP CONSTRAINT wallet_ledger_entry_type_check;
    END IF;
END $$;

ALTER TABLE wallet_ledger ADD CONSTRAINT wallet_ledger_entry_type_check
    CHECK (entry_type IN (
        'subscription_credit',
        'subscription_purchase',
        'topup',
        'usage_charge',
        'usage_deduction',
        'payout_withdrawal',
        'refund',
        'reversal',
        'platform_revenue_allocation',
        'credit',
        'debit'
    ));

-- ---------------------------------------------------------------------
-- C) payout_withdrawals — developer withdrawal transactions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payout_withdrawals (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    withdrawal_id               TEXT UNIQUE NOT NULL,
    project_id                  TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    developer_id                TEXT NULL,
    amount_cents                BIGINT NOT NULL CHECK (amount_cents > 0),
    currency                    CHAR(3) NOT NULL DEFAULT 'ETB',
    payout_account_id           TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'paid', 'failed', 'canceled')),
    processor                   TEXT NOT NULL DEFAULT 'chapa',
    processor_transfer_ref      TEXT NULL,
    error_message               TEXT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payout_withdrawals_project ON payout_withdrawals(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payout_withdrawals_status ON payout_withdrawals(status);
