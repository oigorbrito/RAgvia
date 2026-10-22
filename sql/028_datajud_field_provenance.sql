CREATE TABLE IF NOT EXISTS process_datajud_field_provenance (
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL CHECK (
        field_name IN ('class_name', 'class_code', 'subjects', 'adjudicating_body', 'county')
    ),
    selected_source TEXT NOT NULL CHECK (selected_source IN ('judit', 'datajud')),
    selected_value JSONB,
    conflict BOOLEAN NOT NULL DEFAULT FALSE,
    source_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (version_id, field_name),
    CHECK (
        selected_source <> 'datajud'
        OR (source_ref IS NOT NULL AND length(trim(source_ref)) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_process_datajud_field_provenance_process_version
    ON process_datajud_field_provenance (process_id, version_id);

CREATE OR REPLACE FUNCTION enforce_datajud_field_provenance_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_process_id UUID;
BEGIN
    SELECT process_id
    INTO source_process_id
    FROM process_versions
    WHERE id = NEW.version_id;

    IF source_process_id IS NULL OR source_process_id <> NEW.process_id THEN
        RAISE EXCEPTION 'DataJud field provenance scope does not match process version';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_datajud_field_provenance_scope_check
    ON process_datajud_field_provenance;
CREATE TRIGGER process_datajud_field_provenance_scope_check
BEFORE INSERT OR UPDATE OF process_id, version_id
ON process_datajud_field_provenance
FOR EACH ROW EXECUTE FUNCTION enforce_datajud_field_provenance_scope();
