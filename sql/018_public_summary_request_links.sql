ALTER TABLE public_summary_requests
    ADD COLUMN IF NOT EXISTS tenant_judit_request_id UUID
    REFERENCES tenant_judit_requests(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS public_summary_requests_acquisition_idx
ON public_summary_requests (tenant_judit_request_id)
WHERE tenant_judit_request_id IS NOT NULL;
