ALTER TABLE process_attachments
    ADD COLUMN IF NOT EXISTS source_name TEXT,
    ADD COLUMN IF NOT EXISTS source_date TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS provider_status TEXT;

CREATE INDEX IF NOT EXISTS idx_process_attachments_source_identity
    ON process_attachments (process_id, version_id, source_attachment_id);
