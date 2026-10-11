-- =====================================================================
-- 003_projects_and_api_keys.sql
-- Persistent Projects & API Keys Store for SpendGuard AI.
-- Replaces the in-memory dictionary with durable Postgres tables.
-- =====================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'api_keys_project_id_fkey' AND table_name = 'api_keys'
    ) THEN
        ALTER TABLE api_keys DROP CONSTRAINT api_keys_project_id_fkey;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- A) projects — developer workspaces / customer accounts
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'free'
        CHECK (tier IN ('free', 'builder', 'team', 'enterprise', 'default')),
    owner_id        TEXT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE projects ALTER COLUMN project_id TYPE TEXT USING project_id::text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'Default Project';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'free';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_id TEXT NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE projects ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- ---------------------------------------------------------------------
-- B) api_keys — hashed API tokens for project authentication
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash        TEXT NOT NULL UNIQUE,
    project_id      TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT 'Default Key',
    prefix          TEXT NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ NULL,
    last_used_at    TIMESTAMPTZ NULL
);

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();
ALTER TABLE api_keys ALTER COLUMN project_id TYPE TEXT USING project_id::text;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'Default Key';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS prefix TEXT NOT NULL DEFAULT '';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'api_keys_project_id_fkey' AND table_name = 'api_keys'
    ) THEN
        ALTER TABLE api_keys
            ADD CONSTRAINT api_keys_project_id_fkey
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_project_id ON api_keys(project_id);
CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects(owner_id) WHERE owner_id IS NOT NULL;
