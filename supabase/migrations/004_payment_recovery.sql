-- =====================================================================
-- 004_payment_recovery.sql
-- Payment event recovery & reconciliation schema additions.
-- Ensures reliable retries, backoff scheduling, and audit trail.
-- =====================================================================

ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;
ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ NULL;
ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_payment_events_pending ON payment_events(
    delivered, next_retry_at, retry_count
) WHERE delivered = FALSE;

CREATE INDEX IF NOT EXISTS idx_checkout_sessions_reconcile ON checkout_sessions(
    status, created_at
) WHERE status = 'open';
