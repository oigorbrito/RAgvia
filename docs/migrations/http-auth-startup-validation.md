# HTTP auth startup validation

The API now validates `JUDIT_WEBHOOK_TOKEN` and `RPY_OPS_TOKEN` during lifespan startup, before opening the PostgreSQL pool.

Both values are required, must be non-empty, and must not contain whitespace. This matches how they are transported: the ops credential is a Bearer token whose request value is trimmed, and the Judit credential is carried in a URL path segment. Accepting whitespace would create intermediary-dependent or unusable credentials.

Valid token comparison behavior is unchanged and continues to use constant-time comparison. Invalid configuration now fails deployment startup instead of producing a healthy-looking API whose protected endpoints cannot authenticate.
