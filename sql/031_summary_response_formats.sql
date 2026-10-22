ALTER TABLE process_summaries
ADD COLUMN IF NOT EXISTS structured_output JSONB;

ALTER TABLE public_summary_requests
ADD COLUMN IF NOT EXISTS response_format TEXT NOT NULL DEFAULT 'jsx';

ALTER TABLE public_summary_requests
DROP CONSTRAINT IF EXISTS public_summary_requests_response_format_ck;

ALTER TABLE public_summary_requests
ADD CONSTRAINT public_summary_requests_response_format_ck
CHECK (response_format IN ('jsx', 'json'));
