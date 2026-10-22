CREATE TABLE IF NOT EXISTS judit_trackings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    process_code TEXT NOT NULL,
    provider_tracking_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'creating'
        CHECK (status IN ('creating', 'active', 'deleting', 'deleted', 'failed')),
    recurrence_days INTEGER NOT NULL DEFAULT 1 CHECK (recurrence_days > 0),
    attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    last_event_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, process_code)
);

CREATE INDEX IF NOT EXISTS judit_trackings_tenant_status_idx
    ON judit_trackings (tenant_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS judit_trackings_reconcile_idx
    ON judit_trackings (COALESCE(last_event_at, created_at))
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS judit_tracking_refreshes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_id UUID NOT NULL REFERENCES judit_trackings(id) ON DELETE CASCADE,
    judit_request_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed_retryable', 'failed_ambiguous')),
    reason TEXT NOT NULL DEFAULT 'stale_tracking',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS judit_tracking_refreshes_one_open_idx
    ON judit_tracking_refreshes (tracking_id)
    WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS judit_tracking_refreshes_request_idx
    ON judit_tracking_refreshes (judit_request_id)
    WHERE judit_request_id IS NOT NULL;
