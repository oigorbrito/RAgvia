ALTER TABLE process_summaries
    ADD COLUMN IF NOT EXISTS generation_ms INTEGER;

CREATE INDEX IF NOT EXISTS process_summaries_validation_failed_idx
ON process_summaries ((validation->>'passed'))
WHERE validation->>'passed' = 'false';
