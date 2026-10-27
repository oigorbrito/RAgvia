ALTER TABLE tenant_judit_requests
    ADD COLUMN max_provider_calls SMALLINT NOT NULL DEFAULT 100
        CHECK (max_provider_calls BETWEEN 1 AND 100),
    ADD COLUMN provider_calls_reserved SMALLINT NOT NULL DEFAULT 0
        CHECK (provider_calls_reserved >= 0),
    ADD COLUMN last_provider_call_reserved_at TIMESTAMPTZ;

-- Existing acquisition attempts are conservatively treated as provider calls:
-- a crash may have occurred after Judit accepted a request but before its ID was saved.
UPDATE tenant_judit_requests
SET provider_calls_reserved = LEAST(attempt_number, max_provider_calls);

ALTER TABLE tenant_judit_requests
    ADD CONSTRAINT tenant_judit_provider_calls_within_budget
    CHECK (provider_calls_reserved <= max_provider_calls);

CREATE OR REPLACE FUNCTION protect_tenant_judit_provider_call_budget() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.provider_calls_reserved < OLD.provider_calls_reserved THEN
        RAISE EXCEPTION 'Judit provider call reservations cannot be refunded';
    END IF;
    IF NEW.max_provider_calls > OLD.max_provider_calls THEN
        RAISE EXCEPTION 'Judit provider call budget cannot be expanded';
    END IF;
    IF NEW.provider_calls_reserved = OLD.provider_calls_reserved
       AND NEW.last_provider_call_reserved_at IS DISTINCT FROM OLD.last_provider_call_reserved_at THEN
        RAISE EXCEPTION 'Judit provider reservation timestamp can only advance with a reservation';
    END IF;
    IF NEW.provider_calls_reserved > OLD.provider_calls_reserved
       AND (NEW.last_provider_call_reserved_at IS NULL
            OR (OLD.last_provider_call_reserved_at IS NOT NULL
                AND NEW.last_provider_call_reserved_at < OLD.last_provider_call_reserved_at)) THEN
        RAISE EXCEPTION 'Judit provider reservation timestamp must be monotonic';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tenant_judit_provider_call_budget_immutable
BEFORE UPDATE OF provider_calls_reserved, max_provider_calls, last_provider_call_reserved_at
ON tenant_judit_requests
FOR EACH ROW EXECUTE FUNCTION protect_tenant_judit_provider_call_budget();

CREATE TABLE judit_one_shot_call_reservations (
    run_id TEXT PRIMARY KEY CHECK (run_id ~ '^[A-Z][A-Z0-9-]{5,95}$'),
    tenant_request_id UUID REFERENCES tenant_judit_requests(id) ON DELETE SET NULL,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX judit_one_shot_call_reservations_request_idx
    ON judit_one_shot_call_reservations(tenant_request_id)
    WHERE tenant_request_id IS NOT NULL;

CREATE OR REPLACE FUNCTION protect_judit_one_shot_call_reservation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.reserved_at IS DISTINCT FROM OLD.reserved_at
       OR (NEW.tenant_request_id IS NOT NULL
           AND NEW.tenant_request_id IS DISTINCT FROM OLD.tenant_request_id) THEN
        RAISE EXCEPTION 'Judit one-shot call reservation is immutable except for expunge unlinking';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER judit_one_shot_call_reservation_immutable
BEFORE UPDATE OR DELETE ON judit_one_shot_call_reservations
FOR EACH ROW EXECUTE FUNCTION protect_judit_one_shot_call_reservation();

CREATE OR REPLACE FUNCTION prevent_anthropic_reservation_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Anthropic reservation cannot be deleted';
END;
$$;

CREATE TRIGGER anthropic_reservation_no_delete
BEFORE DELETE ON anthropic_generation_reservations
FOR EACH ROW EXECUTE FUNCTION prevent_anthropic_reservation_delete();
