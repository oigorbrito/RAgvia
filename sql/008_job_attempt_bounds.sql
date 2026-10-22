DO $$
BEGIN
    ALTER TABLE jobs
        ADD CONSTRAINT jobs_max_attempts_bounds_chk
        CHECK (max_attempts BETWEEN 1 AND 100) NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON CONSTRAINT jobs_max_attempts_bounds_chk ON jobs IS
    'New/updated jobs must use max_attempts between 1 and 100. NOT VALID avoids a deployment-time scan of historical rows.';
