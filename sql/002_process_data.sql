CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS processes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    court TEXT,
    class_name TEXT,
    subjects JSONB NOT NULL DEFAULT '[]'::jsonb,
    parties JSONB NOT NULL DEFAULT '[]'::jsonb,
    secrecy_level INTEGER NOT NULL DEFAULT 0,
    header JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS process_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    source_request_id TEXT,
    source_cached_response BOOLEAN NOT NULL DEFAULT FALSE,
    source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    finalized BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalized_at TIMESTAMPTZ,
    UNIQUE (process_id, source_request_id)
);

ALTER TABLE processes
    DROP CONSTRAINT IF EXISTS processes_current_version_id_fkey;
ALTER TABLE processes
    ADD CONSTRAINT processes_current_version_id_fkey
    FOREIGN KEY (current_version_id) REFERENCES process_versions(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS process_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    occurred_at TIMESTAMPTZ,
    title TEXT,
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (version_id, step_number)
);

CREATE INDEX IF NOT EXISTS process_steps_process_idx
ON process_steps (process_id, step_number);

CREATE INDEX IF NOT EXISTS process_steps_version_idx
ON process_steps (version_id, step_number);

CREATE INDEX IF NOT EXISTS process_steps_fts_idx
ON process_steps USING GIN (to_tsvector('portuguese', coalesce(title, '') || ' ' || text));

CREATE INDEX IF NOT EXISTS process_steps_embedding_idx
ON process_steps USING hnsw (embedding vector_cosine_ops)
WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenant_processes (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, process_id)
);

CREATE TABLE IF NOT EXISTS process_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    markdown TEXT NOT NULL,
    validation JSONB NOT NULL DEFAULT '{}'::jsonb,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (process_id, version_id)
);

CREATE TABLE IF NOT EXISTS access_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    process_id UUID REFERENCES processes(id) ON DELETE SET NULL,
    process_code TEXT NOT NULL,
    action TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS access_log_tenant_time_idx
ON access_log (tenant_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION prevent_access_log_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'access_log is immutable';
END;
$$;

DROP TRIGGER IF EXISTS access_log_no_update ON access_log;
CREATE TRIGGER access_log_no_update
BEFORE UPDATE OR DELETE ON access_log
FOR EACH ROW EXECUTE FUNCTION prevent_access_log_mutation();
