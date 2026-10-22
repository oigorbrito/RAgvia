DO $$
BEGIN
    ALTER TABLE processes
        ADD CONSTRAINT processes_code_canonical_cnj_chk
        CHECK (
            code ~ '^[0-9]{7}-[0-9]{2}\.[0-9]{4}\.[0-9]\.[0-9]{2}\.[0-9]{4}$'
        ) NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON CONSTRAINT processes_code_canonical_cnj_chk ON processes IS
    'New/updated process rows must use canonical CNJ formatting. NOT VALID avoids a deployment-time scan of historical rows.';
