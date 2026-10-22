CREATE TABLE IF NOT EXISTS process_summary_sources (
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    chunk_type TEXT NOT NULL CHECK (chunk_type IN ('movement')),
    step_id UUID NOT NULL REFERENCES process_steps(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL CHECK (step_number > 0),
    occurred_at TIMESTAMPTZ,
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (summary_id, step_id),
    UNIQUE (summary_id, source_order)
);

CREATE INDEX IF NOT EXISTS idx_process_summary_sources_process_version
    ON process_summary_sources (process_id, version_id, summary_id);

CREATE INDEX IF NOT EXISTS idx_process_summary_sources_step
    ON process_summary_sources (step_id);
