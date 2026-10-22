ALTER TABLE tenant_judit_requests
    DROP CONSTRAINT IF EXISTS tenant_judit_requests_status_check;

ALTER TABLE tenant_judit_requests
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 1;

UPDATE tenant_judit_requests
SET status = 'failed_ambiguous'
WHERE status = 'failed';

ALTER TABLE tenant_judit_requests
    ADD CONSTRAINT tenant_judit_requests_attempt_number_check
    CHECK (attempt_number BETWEEN 1 AND 100);

ALTER TABLE tenant_judit_requests
    ADD CONSTRAINT tenant_judit_requests_status_check
    CHECK (status IN ('processing', 'completed', 'failed_retryable', 'failed_ambiguous'));
