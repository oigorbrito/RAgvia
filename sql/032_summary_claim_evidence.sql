CREATE TABLE IF NOT EXISTS process_summary_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL CHECK (length(trim(claim_id)) > 0),
    claim_class TEXT NOT NULL CHECK (
        claim_class IN (
            'synthesis', 'current_status', 'procedural_event', 'attention',
            'decision', 'deadline', 'related_process', 'attachment'
        )
    ),
    claim_text TEXT NOT NULL CHECK (length(trim(claim_text)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (summary_id, claim_id)
);

CREATE INDEX IF NOT EXISTS idx_process_summary_claims_scope
    ON process_summary_claims (process_id, version_id, summary_id);

CREATE TABLE IF NOT EXISTS process_summary_claim_sources (
    claim_row_id UUID NOT NULL REFERENCES process_summary_claims(id) ON DELETE CASCADE,
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    evidence_ref TEXT NOT NULL CHECK (evidence_ref ~ '^[pma]-[0-9a-f]{32}$'),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('process', 'movement', 'attachment')),
    step_id UUID REFERENCES process_steps(id) ON DELETE CASCADE,
    attachment_chunk_id UUID REFERENCES attachment_chunks(id) ON DELETE CASCADE,
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (claim_row_id, evidence_ref),
    CHECK (
        (source_kind = 'process' AND step_id IS NULL AND attachment_chunk_id IS NULL)
        OR (source_kind = 'movement' AND step_id IS NOT NULL AND attachment_chunk_id IS NULL)
        OR (source_kind = 'attachment' AND step_id IS NULL AND attachment_chunk_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_process_summary_claim_sources_summary
    ON process_summary_claim_sources (summary_id, source_order);

CREATE INDEX IF NOT EXISTS idx_process_summary_claim_sources_ref
    ON process_summary_claim_sources (evidence_ref);

CREATE OR REPLACE FUNCTION enforce_summary_claim_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    summary_process_id UUID;
    summary_version_id UUID;
BEGIN
    SELECT process_id, version_id
    INTO summary_process_id, summary_version_id
    FROM process_summaries
    WHERE id = NEW.summary_id;

    IF summary_process_id IS NULL
       OR summary_process_id <> NEW.process_id
       OR summary_version_id <> NEW.version_id THEN
        RAISE EXCEPTION 'summary claim scope does not match summary';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_summary_claims_scope_check
    ON process_summary_claims;
CREATE TRIGGER process_summary_claims_scope_check
BEFORE INSERT OR UPDATE OF summary_id, process_id, version_id
ON process_summary_claims
FOR EACH ROW EXECUTE FUNCTION enforce_summary_claim_scope();

CREATE OR REPLACE FUNCTION enforce_summary_claim_source_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    claim_summary_id UUID;
    claim_process_id UUID;
    claim_version_id UUID;
BEGIN
    SELECT summary_id, process_id, version_id
    INTO claim_summary_id, claim_process_id, claim_version_id
    FROM process_summary_claims
    WHERE id = NEW.claim_row_id;

    IF claim_summary_id IS NULL
       OR claim_summary_id <> NEW.summary_id
       OR claim_process_id <> NEW.process_id
       OR claim_version_id <> NEW.version_id THEN
        RAISE EXCEPTION 'claim evidence scope does not match claim';
    END IF;

    IF NEW.source_kind = 'process'
       AND NEW.evidence_ref <> 'p-' || replace(NEW.version_id::text, '-', '') THEN
        RAISE EXCEPTION 'claim process evidence ref does not match version';
    END IF;

    IF NEW.source_kind = 'movement'
       AND NEW.evidence_ref <> 'm-' || replace(NEW.step_id::text, '-', '') THEN
        RAISE EXCEPTION 'claim movement evidence ref does not match step';
    END IF;

    IF NEW.source_kind = 'attachment'
       AND NEW.evidence_ref <> 'a-' || replace(NEW.attachment_chunk_id::text, '-', '') THEN
        RAISE EXCEPTION 'claim attachment evidence ref does not match chunk';
    END IF;

    IF NEW.source_kind = 'movement' AND NOT EXISTS (
        SELECT 1
        FROM process_summary_sources s
        JOIN process_steps ps ON ps.id = s.step_id
        WHERE s.summary_id = NEW.summary_id
          AND s.process_id = NEW.process_id
          AND s.version_id = NEW.version_id
          AND s.step_id = NEW.step_id
          AND ps.process_id = NEW.process_id
          AND ps.version_id = NEW.version_id
    ) THEN
        RAISE EXCEPTION 'claim movement evidence was not used for summary';
    END IF;

    IF NEW.source_kind = 'attachment' AND NOT EXISTS (
        SELECT 1
        FROM process_summary_attachment_sources s
        JOIN attachment_chunks ac ON ac.id = s.attachment_chunk_id
        WHERE s.summary_id = NEW.summary_id
          AND s.process_id = NEW.process_id
          AND s.version_id = NEW.version_id
          AND s.attachment_chunk_id = NEW.attachment_chunk_id
          AND ac.process_id = NEW.process_id
          AND ac.version_id = NEW.version_id
    ) THEN
        RAISE EXCEPTION 'claim attachment evidence was not used for summary';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS process_summary_claim_sources_scope_check
    ON process_summary_claim_sources;
CREATE TRIGGER process_summary_claim_sources_scope_check
BEFORE INSERT OR UPDATE OF claim_row_id, summary_id, process_id, version_id,
    evidence_ref, source_kind, step_id, attachment_chunk_id
ON process_summary_claim_sources
FOR EACH ROW EXECUTE FUNCTION enforce_summary_claim_source_scope();
