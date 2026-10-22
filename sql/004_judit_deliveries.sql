CREATE TABLE IF NOT EXISTS judit_deliveries (
    callback_id TEXT PRIMARY KEY,
    request_id TEXT,
    event_type TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS judit_deliveries_request_idx
ON judit_deliveries (request_id, received_at DESC);
