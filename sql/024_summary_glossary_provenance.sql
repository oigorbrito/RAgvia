CREATE TABLE IF NOT EXISTS process_summary_glossary_sources (
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('class', 'subject')),
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    tpu_version TEXT NOT NULL CHECK (length(trim(tpu_version)) > 0),
    publisher TEXT NOT NULL CHECK (length(trim(publisher)) > 0),
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    source_ref TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
    definition_sha256 TEXT NOT NULL CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (summary_id, kind, code),
    UNIQUE (summary_id, source_order)
);

CREATE INDEX IF NOT EXISTS idx_summary_glossary_sources_process_version
    ON process_summary_glossary_sources (process_id, version_id, summary_id);
