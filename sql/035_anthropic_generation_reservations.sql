-- One-shot cost reservations contain identifiers, never payloads or credentials.
-- Intentionally no process/tenant FK: expunge must not make an approval reusable.
CREATE TABLE IF NOT EXISTS anthropic_generation_reservations (
    approval_id TEXT PRIMARY KEY CHECK (length(approval_id) BETWEEN 6 AND 96),
    owner_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    process_id UUID NOT NULL,
    version_id UUID NOT NULL,
    model TEXT NOT NULL,
    reserved_usd NUMERIC(18,6) NOT NULL CHECK (reserved_usd > 0 AND reserved_usd <> 'NaN'::numeric),
    max_messages INTEGER NOT NULL CHECK (max_messages BETWEEN 1 AND 2),
    messages_reserved INTEGER NOT NULL CHECK (messages_reserved BETWEEN 1 AND max_messages),
    expires_at TIMESTAMPTZ NOT NULL,
    document_sha256 TEXT NOT NULL CHECK (length(document_sha256) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION protect_anthropic_reservation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'messages_reserved') IS DISTINCT FROM
       (to_jsonb(OLD) - 'messages_reserved')
       OR NEW.messages_reserved <> OLD.messages_reserved + 1 THEN
        RAISE EXCEPTION 'Anthropic reservation cannot be renewed or refunded';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS anthropic_reservation_immutable ON anthropic_generation_reservations;
CREATE TRIGGER anthropic_reservation_immutable
BEFORE UPDATE ON anthropic_generation_reservations
FOR EACH ROW EXECUTE FUNCTION protect_anthropic_reservation();
