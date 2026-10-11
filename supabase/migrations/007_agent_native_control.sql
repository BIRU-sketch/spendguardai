-- =====================================================================
-- 007_agent_native_control.sql
-- SpendGuard AI: Agent-Native Hard Cost Control Layer
-- =====================================================================

-- ---------------------------------------------------------------------
-- A) agent_runs — Tracks agent runs, trajectories, spend, and status
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id              TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    agent_id            TEXT NULL,
    session_id          TEXT NULL,
    budget_cents        INT NULL,
    spent_cents         INT NOT NULL DEFAULT 0,
    step_count          INT NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'budget_exceeded', 'runaway_aborted', 'aborted', 'completed')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id) WHERE session_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- B) Enrich ledger_events with agent trajectory identity
-- ---------------------------------------------------------------------
ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS run_id TEXT NULL;
ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS agent_id TEXT NULL;
ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS session_id TEXT NULL;
ALTER TABLE ledger_events ADD COLUMN IF NOT EXISTS cost_cents NUMERIC(12, 6) DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_ledger_events_run_id ON ledger_events(run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ledger_events_agent_id ON ledger_events(agent_id) WHERE agent_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- C) Enrich billing_line_items with agent trajectory identity
-- ---------------------------------------------------------------------
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS run_id TEXT NULL;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS agent_id TEXT NULL;
ALTER TABLE billing_line_items ADD COLUMN IF NOT EXISTS session_id TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_line_items_run_id ON billing_line_items(run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_billing_line_items_agent_id ON billing_line_items(agent_id) WHERE agent_id IS NOT NULL;
