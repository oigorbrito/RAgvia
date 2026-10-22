CREATE TABLE IF NOT EXISTS judit_request_completions (
    request_id TEXT PRIMARY KEY,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
