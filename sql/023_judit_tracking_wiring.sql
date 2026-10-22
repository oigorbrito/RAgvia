ALTER TABLE judit_trackings
    ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS judit_trackings_reconcile_window_idx
    ON judit_trackings (
        GREATEST(
            COALESCE(last_event_at, created_at),
            COALESCE(last_reconciled_at, created_at)
        )
    )
    WHERE status = 'active';

CREATE OR REPLACE FUNCTION reconcile_judit_tracking_process_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    tracking_reference TEXT;
BEGIN
    IF NEW.source_payload->>'reference_type' = 'tracking' THEN
        tracking_reference := NULLIF(NEW.source_payload->>'reference_id', '');
        IF tracking_reference IS NOT NULL THEN
            UPDATE judit_trackings
            SET last_event_at = NOW(), updated_at = NOW()
            WHERE provider_tracking_id = tracking_reference
              AND status = 'active';

            INSERT INTO tenant_processes (tenant_id, process_id)
            SELECT tenant_id, NEW.process_id
            FROM judit_trackings
            WHERE provider_tracking_id = tracking_reference
              AND status = 'active'
            ON CONFLICT DO NOTHING;
        END IF;
    END IF;

    IF NEW.judit_request_id IS NOT NULL THEN
        INSERT INTO tenant_processes (tenant_id, process_id)
        SELECT jt.tenant_id, NEW.process_id
        FROM judit_tracking_refreshes jtr
        JOIN judit_trackings jt ON jt.id = jtr.tracking_id
        WHERE jtr.judit_request_id = NEW.judit_request_id
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_versions_judit_tracking_reconcile ON process_versions;
CREATE TRIGGER process_versions_judit_tracking_reconcile
AFTER INSERT OR UPDATE OF source_payload, judit_request_id ON process_versions
FOR EACH ROW EXECUTE FUNCTION reconcile_judit_tracking_process_version();

CREATE OR REPLACE FUNCTION complete_judit_tracking_refresh()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE judit_tracking_refreshes
    SET status = 'completed', completed_at = COALESCE(completed_at, NOW())
    WHERE judit_request_id = NEW.request_id
      AND status IN ('pending', 'processing');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS judit_request_completion_tracking_refresh ON judit_request_completions;
CREATE TRIGGER judit_request_completion_tracking_refresh
AFTER INSERT ON judit_request_completions
FOR EACH ROW EXECUTE FUNCTION complete_judit_tracking_refresh();
