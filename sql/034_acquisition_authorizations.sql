CREATE TABLE IF NOT EXISTS acquisition_authorizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'judit',
    operation TEXT NOT NULL DEFAULT 'refresh',
    token TEXT NOT NULL UNIQUE,
    idempotency_key TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by_request_id UUID REFERENCES tenant_judit_requests(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS acquisition_authorizations_tenant_code_idx
    ON acquisition_authorizations (tenant_id, process_code, token);

CREATE INDEX IF NOT EXISTS acquisition_authorizations_token_idx
    ON acquisition_authorizations (token)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS tenant_judit_request_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_request_id UUID NOT NULL REFERENCES tenant_judit_requests(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL,
    judit_request_id TEXT,
    status TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tenant_judit_request_history_tenant_code_idx
    ON tenant_judit_request_history (tenant_id, process_code, archived_at DESC);
