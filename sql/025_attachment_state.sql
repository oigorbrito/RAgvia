CREATE TABLE IF NOT EXISTS process_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    source_attachment_id TEXT NOT NULL CHECK (length(trim(source_attachment_id)) > 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'ready', 'unavailable', 'corrupt', 'unreadable')
    ),
    content_type TEXT,
    byte_size BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),
    content_sha256 TEXT CHECK (
        content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (version_id, source_attachment_id)
);

CREATE INDEX IF NOT EXISTS idx_process_attachments_process_version
    ON process_attachments (process_id, version_id, status);

CREATE TABLE IF NOT EXISTS attachment_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attachment_id UUID NOT NULL REFERENCES process_attachments(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    text TEXT NOT NULL CHECK (length(text) > 0),
    page_start INTEGER,
    page_end INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((page_start IS NULL) = (page_end IS NULL)),
    CHECK (page_start IS NULL OR page_start >= 1),
    CHECK (page_end IS NULL OR page_end >= page_start),
    CHECK ((char_start IS NULL) = (char_end IS NULL)),
    CHECK (char_start IS NULL OR char_start >= 0),
    CHECK (char_end IS NULL OR char_end >= char_start),
    UNIQUE (attachment_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_attachment_chunks_process_version
    ON attachment_chunks (process_id, version_id, attachment_id, chunk_index);

CREATE OR REPLACE FUNCTION enforce_attachment_scope_consistency()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    attachment_process_id UUID;
    attachment_version_id UUID;
    version_process_id UUID;
BEGIN
    SELECT process_id INTO version_process_id
    FROM process_versions
    WHERE id = NEW.version_id;

    IF version_process_id IS NULL OR version_process_id <> NEW.process_id THEN
        RAISE EXCEPTION 'attachment version does not belong to process';
    END IF;

    IF TG_TABLE_NAME = 'attachment_chunks' THEN
        SELECT process_id, version_id
        INTO attachment_process_id, attachment_version_id
        FROM process_attachments
        WHERE id = NEW.attachment_id;

        IF attachment_process_id IS NULL
           OR attachment_process_id <> NEW.process_id
           OR attachment_version_id <> NEW.version_id THEN
            RAISE EXCEPTION 'attachment chunk scope does not match attachment';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_attachments_scope_check ON process_attachments;
CREATE TRIGGER process_attachments_scope_check
BEFORE INSERT OR UPDATE OF process_id, version_id ON process_attachments
FOR EACH ROW EXECUTE FUNCTION enforce_attachment_scope_consistency();

DROP TRIGGER IF EXISTS attachment_chunks_scope_check ON attachment_chunks;
CREATE TRIGGER attachment_chunks_scope_check
BEFORE INSERT OR UPDATE OF attachment_id, process_id, version_id ON attachment_chunks
FOR EACH ROW EXECUTE FUNCTION enforce_attachment_scope_consistency();
