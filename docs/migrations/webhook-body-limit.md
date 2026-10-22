# Judit webhook body-size boundary

## Scope

Bound inbound Judit webhook payloads before JSON parsing or database work.

## Runtime contract

`JUDIT_WEBHOOK_MAX_BODY_BYTES` defaults to 5 MiB (`5242880`). Invalid or non-positive configuration fails application startup.

The application middleware applies the same byte ceiling to every inbound `POST`, including `POST /webhooks/judit/*`, `/v1/resumos`, and tracking batch requests. This preserves the webhook limit as the deployment-wide POST ceiling and enforces it in two layers:

- rejects an oversized declared `Content-Length` before the route runs
- counts streamed ASGI request chunks so requests without `Content-Length` or with a forged header cannot bypass the limit

A request that exceeds the limit returns HTTP 413 and does not reach webhook parsing, staging or queue writes.

## Deployment note

A reverse proxy or ingress should still enforce an equal or smaller request-body limit. The application-side check is the final boundary when traffic reaches Uvicorn directly or proxy configuration drifts.
