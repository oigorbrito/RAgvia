# API key security contract

Rpy supports tenant API keys for process-facing HTTP access while retaining the legacy bearer-token mapping only as a migration compatibility path.

## Key format and storage

Application API keys use one of two prefixes:

- `sk_live_` for production credentials;
- `sk_test_` for non-production credentials.

The complete key is presented by the client as `Authorization: Bearer <key>`. Rpy never persists the complete key. PostgreSQL stores only:

- SHA-256 of the complete high-entropy key for lookup;
- a short SHA-256 fingerprint for audit correlation;
- tenant, environment, lifecycle and scope metadata.

The deployment must set `RPY_API_KEY_ENVIRONMENT` to `live` or `test`. A key
whose prefix environment does not match the deployment is rejected. The
default is `live`, so test keys cannot be accepted accidentally in production.

The plaintext key must be shown only at provisioning time by the operational provisioning mechanism. It must not be placed in repository files, fixtures, logs, screenshots, tickets or access-log metadata.

## Scope

Each API key may authorize one or both of these mechanisms:

- explicit CNJ scope in `api_key_cnj_scopes`;
- the tenant's existing process portfolio through `tenant_processes` when `allow_portfolio=true`.

An explicit CNJ scope can authorize acquisition of a process before that process exists locally. Portfolio scope only authorizes processes already bound to that tenant. A process bound only to another tenant never becomes visible through portfolio scope.

Authorization is evaluated before process reads and before a process acquisition job is created. A valid key outside its scope receives the same process-not-found response shape used to avoid cross-tenant existence disclosure.

Future process-derived surfaces such as sources, attachments and related-process retrieval must reuse the same tenant/API-key principal and must not bypass this scope check.

The existing `/v1/processos/{cnj}/fontes` surface is included in this
authorization boundary.

## Rate limiting

Each API key has a `rate_limit_per_minute`. Rpy uses a PostgreSQL-backed fixed window with a row lock per key; Redis is not required.

Successful authorization consumes one request from the current window. Responses made with an API key include `X-RateLimit-Remaining`. When the limit is exhausted, Rpy returns HTTP 429 with:

- `X-RateLimit-Remaining: 0`;
- `Retry-After` with the remaining window duration in seconds.

## Revocation, expiration and rotation

Revocation sets `revoked_at`; revoked credentials fail authentication immediately. Optional `expires_at` provides automatic expiry.

Rotation is performed by provisioning a new key row and scopes first, switching clients to the new credential, then revoking the old row. Rotation must never reuse the same secret or overwrite a prior key hash. Audit history keeps the old key UUID/fingerprint so historical access remains attributable without retaining the secret.

Deleting a tenant removes its API-key rows through the existing tenant cascade. Access-log records remain subject to the repository's immutable-audit and retention policy; key foreign keys become null where required, while the non-secret fingerprint can remain for historical correlation.

## Audit and LGPD minimization

API-key access records include the tenant, CNJ, action, key UUID and non-secret fingerprint. They must not include:

- the plaintext API key or bearer credential;
- raw Judit payloads;
- prompts or provider responses;
- process movement text, party identifiers or other unnecessary PII.

Scope denials are auditable without loading process content. Rate-limit state contains only key identity, counters and timestamps.

## Legacy bearer compatibility

`RPY_BEARER_TOKENS` remains accepted for the pre-existing process endpoints during migration. Legacy bearer tokens do not gain API-key lifecycle, scope or per-key rate-limit semantics. New external integrations should use tenant API keys so authorization, lifecycle and audit identity are explicit.
