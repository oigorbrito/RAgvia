CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL UNIQUE,
    environment TEXT NOT NULL CHECK (environment IN ('live', 'test')),
    allow_portfolio BOOLEAN NOT NULL DEFAULT FALSE,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60
        CHECK (rate_limit_per_minute BETWEEN 1 AND 100000),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS api_keys_tenant_active_idx
ON api_keys (tenant_id, created_at DESC)
WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS api_key_cnj_scopes (
    api_key_id UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL CHECK (
        process_code ~ '^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_key_id, process_code)
);

CREATE INDEX IF NOT EXISTS api_key_cnj_scopes_code_idx
ON api_key_cnj_scopes (process_code, api_key_id);

-- One mutable fixed-window counter per key. This keeps rate limiting durable
-- without creating an unbounded request-log table or introducing Redis.
CREATE TABLE IF NOT EXISTS api_key_rate_limits (
    api_key_id UUID PRIMARY KEY REFERENCES api_keys(id) ON DELETE CASCADE,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT date_trunc('minute', NOW()),
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE access_log
    ADD COLUMN IF NOT EXISTS api_key_id UUID REFERENCES api_keys(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS api_key_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS access_log_api_key_time_idx
ON access_log (api_key_id, occurred_at DESC)
WHERE api_key_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS access_log_api_key_fingerprint_time_idx
ON access_log (api_key_fingerprint, occurred_at DESC)
WHERE api_key_fingerprint IS NOT NULL;
