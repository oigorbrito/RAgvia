DO $$ BEGIN
    CREATE TYPE public_summary_status AS ENUM (
        'queued',
        'fetching',
        'indexing',
        'generating',
        'validating',
        'completed',
        'failed',
        'source_unavailable',
        'secrecy_blocked',
        'validation_failed'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS public_summary_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status public_summary_status NOT NULL DEFAULT 'queued',
    tenant_judit_request_id UUID REFERENCES tenant_judit_requests(id) ON DELETE SET NULL,
    process_id UUID REFERENCES processes(id) ON DELETE SET NULL,
    version_id UUID REFERENCES process_versions(id) ON DELETE SET NULL,
    summary_id UUID REFERENCES process_summaries(id) ON DELETE SET NULL,
    source_updated_at TIMESTAMPTZ,
    flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS public_summary_requests_tenant_status_idx
ON public_summary_requests (tenant_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS public_summary_requests_process_idx
ON public_summary_requests (tenant_id, process_code, updated_at DESC);

CREATE INDEX IF NOT EXISTS public_summary_requests_acquisition_idx
ON public_summary_requests (tenant_judit_request_id, updated_at DESC)
WHERE tenant_judit_request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS public_summary_requests_version_idx
ON public_summary_requests (version_id, updated_at DESC)
WHERE version_id IS NOT NULL;

ALTER TABLE public_summary_requests
    DROP CONSTRAINT IF EXISTS public_summary_requests_terminal_time_ck;
ALTER TABLE public_summary_requests
    ADD CONSTRAINT public_summary_requests_terminal_time_ck CHECK (
        (status IN ('completed', 'failed', 'source_unavailable', 'secrecy_blocked', 'validation_failed') AND completed_at IS NOT NULL)
        OR
        (status NOT IN ('completed', 'failed', 'source_unavailable', 'secrecy_blocked', 'validation_failed') AND completed_at IS NULL)
    );
