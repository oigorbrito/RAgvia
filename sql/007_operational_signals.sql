CREATE TABLE IF NOT EXISTS backup_runs (
    id BIGSERIAL PRIMARY KEY,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archive_name TEXT NOT NULL,
    archive_bytes BIGINT NOT NULL CHECK (archive_bytes >= 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS backup_runs_completed_at_idx
ON backup_runs (completed_at DESC);
