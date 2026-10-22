ALTER TABLE tenant_judit_requests
    ALTER COLUMN judit_request_id DROP NOT NULL;

ALTER TABLE tenant_judit_requests
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'processing';

ALTER TABLE tenant_judit_requests
    ADD CONSTRAINT tenant_judit_requests_status_check
    CHECK (status IN ('processing', 'completed', 'failed'));
