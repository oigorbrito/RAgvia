ALTER TABLE process_summaries
    ADD COLUMN IF NOT EXISTS usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(18, 8);

ALTER TABLE process_summaries
    DROP CONSTRAINT IF EXISTS process_summaries_cost_usd_nonnegative_ck;
ALTER TABLE process_summaries
    ADD CONSTRAINT process_summaries_cost_usd_nonnegative_ck
    CHECK (cost_usd IS NULL OR cost_usd >= 0);
