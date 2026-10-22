# Webhook secret redaction

The Judit callback URL currently authenticates with an opaque token in the path because the documented callback contract does not provide an application-controlled signature or custom authentication header.

A URL credential must not become a durable logging credential. `JuditWebhookSecretRedactionMiddleware` therefore:

- recognizes the `/webhooks/judit/` prefix for application-side path redaction;
- for the exact `/webhooks/judit/<token>` route, copies the original token into request-local ASGI state;
- replaces the first token-like path segment in both `scope.path` and `scope.raw_path` with `__redacted__` before FastAPI handles the request;
- preserves any trailing slash/additional path remainder and does not copy the original token into authentication state for malformed paths, so redaction cannot turn an invalid request into a valid authenticated route;
- lets the existing valid route keep the same public callback shape;
- authenticates valid callbacks using the request-local original value with constant-time comparison.

The redaction protects application/Uvicorn logging that derives request paths from the ASGI scope. Reverse proxies, CDNs and ingress controllers see the URL before it reaches the application and must independently redact or suppress the webhook path in access logs.

Do not include `JUDIT_WEBHOOK_TOKEN` in structured logs, exception metadata, tracing attributes, metrics labels or audit-log metadata. Token rotation still requires changing the configured callback URL and `JUDIT_WEBHOOK_TOKEN` together.
