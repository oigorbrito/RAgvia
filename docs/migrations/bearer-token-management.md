# Bearer token management

## Scope

Keep tenant bearer-token configuration simple and production-safe without adding Redis, an auth service, or another runtime dependency.

## Configuration

`RPY_BEARER_TOKENS` is a JSON object mapping bearer token strings to tenant UUIDs. The application parses and validates this mapping once during API startup. Malformed JSON, non-object values, empty tokens, non-string tenant ids, or invalid UUIDs fail startup rather than surfacing as request-time 500 errors.

The parsed mapping is stored in process memory for request authentication. Changes therefore require an API restart/redeploy, which is deliberate: secret changes should pass through the deployment/secret-management path rather than mutate process state implicitly.

## Rotation without downtime

The mapping permits multiple tokens to point to the same tenant. Rotation procedure:

1. add the new random token while retaining the old token and deploy
2. move callers to the new token
3. remove the old token and deploy again

Both tokens authorize the same tenant during the overlap window.

## Production secret handling

Do not commit token values or place them in images. Inject `RPY_BEARER_TOKENS` through the platform secret manager. Use high-entropy random tokens and maintain separate values per environment.

Authentication still uses constant-time token comparison and tenant authorization remains enforced in PostgreSQL through `tenant_processes`.
