CREATE TABLE IF NOT EXISTS tenant_judit_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL,
    judit_request_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, process_code)
);

CREATE INDEX IF NOT EXISTS tenant_judit_requests_code_idx
ON tenant_judit_requests (process_code);
