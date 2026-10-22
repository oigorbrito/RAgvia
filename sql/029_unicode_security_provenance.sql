ALTER TABLE process_summaries
    ADD COLUMN IF NOT EXISTS unicode_security_flags JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE process_summaries
    DROP CONSTRAINT IF EXISTS process_summaries_unicode_security_flags_array_ck;
ALTER TABLE process_summaries
    ADD CONSTRAINT process_summaries_unicode_security_flags_array_ck
    CHECK (jsonb_typeof(unicode_security_flags) = 'array');

ALTER TABLE process_summary_sources
    ADD COLUMN IF NOT EXISTS source_text_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS unicode_security_flags JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE process_summary_sources
    DROP CONSTRAINT IF EXISTS process_summary_sources_text_sha256_ck;
ALTER TABLE process_summary_sources
    ADD CONSTRAINT process_summary_sources_text_sha256_ck
    CHECK (source_text_sha256 IS NULL OR source_text_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE process_summary_sources
    DROP CONSTRAINT IF EXISTS process_summary_sources_unicode_flags_array_ck;
ALTER TABLE process_summary_sources
    ADD CONSTRAINT process_summary_sources_unicode_flags_array_ck
    CHECK (jsonb_typeof(unicode_security_flags) = 'array');

ALTER TABLE process_summary_attachment_sources
    ADD COLUMN IF NOT EXISTS unicode_security_flags JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE process_summary_attachment_sources
    DROP CONSTRAINT IF EXISTS process_summary_attachment_sources_unicode_flags_array_ck;
ALTER TABLE process_summary_attachment_sources
    ADD CONSTRAINT process_summary_attachment_sources_unicode_flags_array_ck
    CHECK (jsonb_typeof(unicode_security_flags) = 'array');
