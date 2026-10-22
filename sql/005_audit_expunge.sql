ALTER TABLE access_log
    DROP CONSTRAINT IF EXISTS access_log_process_id_fkey;

-- access_log intentionally keeps the historical process UUID and CNJ after expunge.
-- A foreign key would either block deletion or mutate the immutable audit row.
COMMENT ON COLUMN access_log.process_id IS
    'Historical process UUID. Intentionally not FK-constrained so audit rows survive LGPD expunge unchanged.';
