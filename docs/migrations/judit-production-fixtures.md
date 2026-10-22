# Judit production-shaped fixtures

## Scope

Lock the webhook parser to the production envelope documented publicly by Judit while keeping all committed fixtures synthetic and free of real case data.

## Source contract

Judit documents `response_created` callbacks with `reference_type` equal to `request` or `tracking`, a payload-level `request_id`, `response_id`, `response_type`, `response_data`, and `tags.cached_response`.

For tracking callbacks, `reference_id` identifies the tracking subscription. It must not replace `payload.request_id`, which identifies the concrete request execution used to correlate lawsuit responses and finalization.

Judit also documents end-of-delivery as `response_type = application_info` with `response_data.code = 600` and/or `response_data.message = REQUEST_COMPLETED`. Rpy treats this as request completion and enqueues the durable finalizer.

## Fixtures

Files under `tests/fixtures/judit/` are sanitized, production-shaped examples. They contain only synthetic CNJs, party names, identifiers and movement text; no customer or real lawsuit payload is committed.

## Regression coverage

- tracking lawsuit callbacks prefer `payload.request_id` over `reference_id`
- `application_info` code 600 / `REQUEST_COMPLETED` is recognized as completion
- unrelated `application_info` does not finalize
- PostgreSQL webhook integration enqueues `judit-finalize:<request_id>` and never `judit-finalize:<tracking_id>` for this flow
- promotable fields are extracted from the production-shaped lawsuit fixture without persisting document identifiers
