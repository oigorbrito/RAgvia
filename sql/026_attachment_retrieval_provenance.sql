CREATE INDEX IF NOT EXISTS idx_attachment_chunks_text_fts
    ON attachment_chunks
    USING GIN (to_tsvector('portuguese', text));

CREATE TABLE IF NOT EXISTS process_summary_attachment_sources (
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    attachment_id UUID NOT NULL REFERENCES process_attachments(id) ON DELETE CASCADE,
    attachment_chunk_id UUID NOT NULL REFERENCES attachment_chunks(id) ON DELETE CASCADE,
    source_attachment_id TEXT NOT NULL CHECK (length(trim(source_attachment_id)) > 0),
    page_start INTEGER,
    page_end INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (summary_id, attachment_chunk_id),
    UNIQUE (summary_id, source_order),
    CHECK ((page_start IS NULL) = (page_end IS NULL)),
    CHECK (page_start IS NULL OR page_start >= 1),
    CHECK (page_end IS NULL OR page_end >= page_start),
    CHECK ((char_start IS NULL) = (char_end IS NULL)),
    CHECK (char_start IS NULL OR char_start >= 0),
    CHECK (char_end IS NULL OR char_end >= char_start)
);

CREATE INDEX IF NOT EXISTS idx_process_summary_attachment_sources_summary
    ON process_summary_attachment_sources (summary_id, source_order);

CREATE OR REPLACE FUNCTION enforce_summary_attachment_source_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    summary_process_id UUID;
    summary_version_id UUID;
    chunk_process_id UUID;
    chunk_version_id UUID;
    chunk_attachment_id UUID;
    attachment_source_id TEXT;
BEGIN
    SELECT process_id, version_id
    INTO summary_process_id, summary_version_id
    FROM process_summaries
    WHERE id = NEW.summary_id;

    SELECT process_id, version_id, attachment_id
    INTO chunk_process_id, chunk_version_id, chunk_attachment_id
    FROM attachment_chunks
    WHERE id = NEW.attachment_chunk_id;

    SELECT source_attachment_id
    INTO attachment_source_id
    FROM process_attachments
    WHERE id = NEW.attachment_id;

    IF summary_process_id IS NULL
       OR summary_process_id <> NEW.process_id
       OR summary_version_id <> NEW.version_id THEN
        RAISE EXCEPTION 'summary attachment provenance scope does not match summary';
    END IF;

    IF chunk_process_id IS NULL
       OR chunk_process_id <> NEW.process_id
       OR chunk_version_id <> NEW.version_id
       OR chunk_attachment_id <> NEW.attachment_id THEN
        RAISE EXCEPTION 'summary attachment provenance scope does not match chunk';
    END IF;

    IF attachment_source_id IS NULL
       OR attachment_source_id <> NEW.source_attachment_id THEN
        RAISE EXCEPTION 'summary attachment provenance source id does not match attachment';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_summary_attachment_sources_scope_check
    ON process_summary_attachment_sources;
CREATE TRIGGER process_summary_attachment_sources_scope_check
BEFORE INSERT OR UPDATE OF summary_id, process_id, version_id, attachment_id,
    attachment_chunk_id, source_attachment_id
ON process_summary_attachment_sources
FOR EACH ROW EXECUTE FUNCTION enforce_summary_attachment_source_scope();
