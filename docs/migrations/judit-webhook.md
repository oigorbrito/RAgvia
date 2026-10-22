# Block 3: Judit webhook ingestion

## Endpoint

`POST /webhooks/judit/{token}`

## Invariants

- Invalid token returns HTTP 404.
- The request performs no LLM call.
- Every accepted payload is staged into `process_versions`.
- Promotion into the current process state occurs only on `request_completed`.
- `cached_response=true` is stored/versioned but does not enqueue summary generation.
- `cached_response=false` + completed request enqueues `generate_process_summary` idempotently.

## Adapter boundary

`app/judit.py` contains field normalization. When real Judit payload fixtures are available, adjust this adapter rather than spreading provider-specific names through the domain layer.

## Pending hardening

- Validate behavior against real Judit webhook fixtures/documentation.
- Add request-body size limits.
- Add integration tests proving the HTTP response path stays below the required latency budget under normal database load.
