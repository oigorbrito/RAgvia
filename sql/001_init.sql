CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
    CREATE TYPE job_status AS ENUM ('pending', 'processing', 'completed', 'dead');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status job_status NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 10,
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id UUID,
    last_heartbeat TIMESTAMPTZ,
    result JSONB,
    error_log TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key_uq
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS jobs_claim_idx
ON jobs (status, priority, run_at)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS jobs_heartbeat_idx
ON jobs (last_heartbeat)
WHERE status = 'processing';
