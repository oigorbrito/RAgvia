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
    ) THEN
        RAISE EXCEPTION 'finalized process version source data is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    CREATE TRIGGER process_versions_finalized_source_immutable
    BEFORE UPDATE ON process_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_finalized_version_source_mutation();
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON FUNCTION prevent_finalized_version_source_mutation() IS
    'Prevents source identity/payload mutation after a process version has been finalized.';
