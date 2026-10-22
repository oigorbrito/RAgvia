ALTER TABLE process_versions
    ADD COLUMN IF NOT EXISTS semantic_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS semantic_schema_version INTEGER,
    ADD COLUMN IF NOT EXISTS equivalent_to_version_id UUID REFERENCES process_versions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS process_versions_semantic_fingerprint_idx
ON process_versions (process_id, semantic_schema_version, semantic_fingerprint)
WHERE semantic_fingerprint IS NOT NULL;

CREATE OR REPLACE FUNCTION prevent_finalized_version_source_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.finalized AND (
        NEW.source_request_id IS DISTINCT FROM OLD.source_request_id
        OR NEW.source_cached_response IS DISTINCT FROM OLD.source_cached_response
        OR NEW.source_payload IS DISTINCT FROM OLD.source_payload
        OR NEW.judit_request_id IS DISTINCT FROM OLD.judit_request_id
        OR NEW.judit_response_id IS DISTINCT FROM OLD.judit_response_id
        OR NEW.judit_callback_id IS DISTINCT FROM OLD.judit_callback_id
        OR NEW.semantic_fingerprint IS DISTINCT FROM OLD.semantic_fingerprint
        OR NEW.semantic_schema_version IS DISTINCT FROM OLD.semantic_schema_version
        OR NEW.equivalent_to_version_id IS DISTINCT FROM OLD.equivalent_to_version_id
    ) THEN
        RAISE EXCEPTION 'finalized process version source data is immutable';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN process_versions.semantic_fingerprint IS
    'SHA-256 of the versioned normalized process semantics used for no-change detection.';
COMMENT ON COLUMN process_versions.semantic_schema_version IS
    'Version of the normalized semantic document used to compute semantic_fingerprint.';
COMMENT ON COLUMN process_versions.equivalent_to_version_id IS
    'Current finalized version with identical normalized semantics when this staged response produced no process change.';
