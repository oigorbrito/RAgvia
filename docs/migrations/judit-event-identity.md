# Judit actionable event identity

Webhook events that create durable side effects now require the identifiers needed for idempotent staging/finalization.

A lawsuit `response_created` event must include a request id and at least one stable response identifier (`response_id` or `callback_id`) in addition to the process code. Without a request id the staged version cannot be paired with the later completion event; without either stable response identifier retries cannot be deduplicated safely because a nullable source key does not conflict in PostgreSQL.

A recognized request-completion event must include a request id so the finalizer can address the staged execution. Non-completion `application_info` events remain parseable without a request id because they do not trigger durable work.

The API already converts `parse_event()` validation failures into HTTP 400, so malformed actionable events are rejected before delivery persistence, staging, or queue enqueue.
