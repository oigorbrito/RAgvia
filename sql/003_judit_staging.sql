ALTER TABLE process_versions
    ADD COLUMN IF NOT EXISTS judit_request_id TEXT,
    ADD COLUMN IF NOT EXISTS judit_response_id TEXT,
    ADD COLUMN IF NOT EXISTS judit_callback_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS process_versions_judit_response_uq
ON process_versions (judit_response_id)
WHERE judit_response_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS process_versions_judit_callback_uq
ON process_versions (judit_callback_id)
WHERE judit_callback_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS process_versions_judit_request_idx
ON process_versions (judit_request_id, source_cached_response, created_at DESC)
WHERE judit_request_id IS NOT NULL;
