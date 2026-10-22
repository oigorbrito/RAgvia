# Production deployment contract

`compose.yaml` remains the developer-oriented stack. Production deployments use `compose.production.yaml` and must preserve the boundaries below.

## Immutable application artifact

Production does not build application source on the deployment host. `RPY_IMAGE` is required and must be a registry reference pinned by a full SHA-256 digest, for example:

`ghcr.io/oigorbrito/rpy@sha256:<64-hex-digest>`

The same exact digest is used by `migrate`, `api`, the egress proxy, both workers and `scheduler`. Tags such as `latest`, `main`, semantic-version tags and commit-SHA tags are useful aliases for humans and release automation, but are not accepted as the production deployment identity because a tag can be moved.

Build and publish the image once in trusted CI. Record the resulting registry digest as release metadata. Promote that digest unchanged through environments; do not rebuild for staging or production. This keeps migration code and runtime code on the same artifact revision.

The image publication workflow also creates a GitHub artifact attestation for the exact published digest after the digest passes the runtime smoke. Before promotion, an operator with GitHub CLI access can verify the build provenance:

```bash
export RPY_IMAGE='ghcr.io/oigorbrito/rpy@sha256:<64-hex-digest>'
gh attestation verify "oci://${RPY_IMAGE}" -R oigorbrito/rpy
```

Attestation proves provenance (repository/workflow/commit/build identity); it is not a substitute for vulnerability review, runtime smoke, tests or deployment approval.

Rollback is artifact selection, not reconstruction: set `RPY_IMAGE` back to a previously known-good digest and execute the normal deployment sequence. Database migrations still determine whether an application rollback is schema-compatible, so destructive migrations require their own explicit rollback plan.

## Topology

The minimum supported topology is:

- one always-warm API service;
- two worker processes consuming the PostgreSQL queue;
- exactly one scheduler process;
- one migration job per deploy;
- one allowlisted egress proxy for provider-bound HTTPS traffic;
- PostgreSQL 16 with pgvector.

The API is intentionally configured with one Uvicorn worker. Horizontal API scaling, when needed, should happen by adding API replicas behind the ingress rather than by increasing the in-container Uvicorn worker count. Workers may scale horizontally because queue claims are fenced through PostgreSQL. The scheduler must remain singleton.

## Network boundary

PostgreSQL does not publish a host port and only joins the internal `backend` network. `migrate`, `api` and `scheduler` are also backend-only and therefore have no Docker route to the external network.

Workers join `backend` plus a second internal network named `provider-gateway`. They do **not** join `egress`. HTTPS provider clients receive `HTTPS_PROXY=http://egress-proxy:3128`; the `egress-proxy` service is the only application service attached to both `provider-gateway` and the externally routed `egress` network. The proxy accepts only HTTP `CONNECT` to port 443 and only for exact DNS names in `EGRESS_PROXY_ALLOWED_HOSTS`; IP literals, wildcard hostnames, plain HTTP forwarding and non-443 ports are rejected.

This topology makes the network route itself a control: a compromised worker cannot bypass the proxy by opening a direct internet socket because neither of its networks has an external gateway. The proxy has no database connection and receives no provider/API credentials. It is a transport gateway, not an application credential broker.

The minimum allowlist depends on activated features. Anthropic plus Judit request/tracking hosts are always required. The legacy OpenAI embedding path additionally requires `api.openai.com`. Cohere requires `api.cohere.com` only when external embedding or reranking is explicitly enabled. Judit attachment download additionally requires `lawsuits.production.judit.io`. Enabled DataJud and Langfuse require the exact hostnames from their configured HTTPS base URLs. `scripts/validate_deploy_env.py` checks these relationships before deployment.

Adding a new provider hostname is an explicit security change:

1. verify the provider integration and legal/product authorization;
2. add only the exact DNS hostname to `EGRESS_PROXY_ALLOWED_HOSTS` in the environment-specific secret/config source;
3. update preflight logic/tests when the hostname is required by a repository-supported feature;
4. run deploy preflight and production Compose validation;
5. do not use wildcards or IP-address allowlists to avoid DNS-name review.

The API publishes port 8000 on `127.0.0.1` by default. Put a TLS-terminating reverse proxy or ingress in front of it. If the ingress runs on a different host/network, change `RPY_API_BIND_ADDRESS` deliberately and apply an equivalent network policy/firewall rule instead of exposing PostgreSQL.

The ingress should enforce a request-body limit no greater than `JUDIT_WEBHOOK_MAX_BODY_BYTES` and should preserve the application's `/health` and `/ready` behavior. `/health` is liveness-only; `/ready` verifies PostgreSQL reachability.

This follows Docker Compose's documented `internal: true` network isolation model and OWASP SSRF guidance to enforce allowed outbound routes at the network layer in addition to application validation:
- https://docs.docker.com/reference/compose-file/networks/
- https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html

## Runtime confinement

All application-image services (`migrate`, `api`, `egress-proxy`, both workers and `scheduler`) run as UID/GID `10001:10001` with a read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges:true`, and an explicit `/tmp` tmpfs mounted with `noexec,nosuid,nodev`. No application service may add Linux capabilities, use privileged mode, use host networking, or opt out of Docker's default seccomp policy.

Docker documents the default seccomp profile as a moderately protective allowlist and recommends not changing it without a concrete compatibility requirement. Rpy therefore relies on Docker's built-in/default seccomp profile and mechanically rejects `seccomp=unconfined` rather than carrying a custom profile without measured need:
- https://docs.docker.com/engine/security/seccomp/

The Compose contract also sets CPU, memory and PID ceilings. These are safety ceilings rather than performance SLOs:

| Service | CPU | Memory | PIDs |
|---|---:|---:|---:|
| `migrate` | 1.0 | 512 MiB | 128 |
| `api` | 1.0 | 512 MiB | 128 |
| `egress-proxy` | 0.5 | 256 MiB | 128 |
| each worker | 2.0 | 8 GiB | 256 |
| `scheduler` | 0.5 | 256 MiB | 64 |

The worker memory ceiling is intentionally conservative because one worker can load local embedding and reranker models in the same process. The repository does not claim that 8 GiB is an empirically optimal production allocation. Before reducing the ceiling—or increasing it in response to real model/runtime observations—capture RSS/CPU/PID measurements under the selected BGE/reranker configuration and change the Compose contract together with its validator/tests.

Writable model caches are redirected to tmpfs (`HF_HOME=/tmp/huggingface`, `XDG_CACHE_HOME=/tmp/.cache`). Production BGE artifacts themselves remain pre-provisioned at the configured read-only path; runtime model downloads are not part of the production contract.

## Browser security headers

The application applies a deterministic browser-security baseline to every HTTP response, including frontend assets, JSON/API responses and errors:

- `Content-Security-Policy: default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; frame-src 'none'; form-action 'self'`;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY` as legacy defense in depth alongside CSP `frame-ancestors 'none'`;
- `Referrer-Policy: no-referrer`;
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`.

The frontend deliberately uses only same-origin external JavaScript/CSS and requires no `unsafe-inline` or `unsafe-eval` CSP exception. A new browser capability or external frontend origin must be treated as a security-contract change and accompanied by tests before the policy is relaxed.

HTTP Strict Transport Security (HSTS) remains owned by the TLS-terminating ingress/reverse proxy, not the application container. Enable HSTS only where HTTPS is actually authoritative for the public hostname; do not infer HTTPS from the internal HTTP hop between ingress and Rpy.

## Secrets

Production has no fallback values for:

- `POSTGRES_PASSWORD`;
- `MIGRATION_DATABASE_URL`;
- `API_DATABASE_URL`;
- `WORKER_DATABASE_URL`;
- `SCHEDULER_DATABASE_URL`;
- `BACKUP_DATABASE_URL`;
- `ANTHROPIC_API_KEY`;
- `JUDIT_API_KEY`;
- `JUDIT_CALLBACK_URL`;
- `JUDIT_WEBHOOK_TOKEN`;
- `RPY_BEARER_TOKENS`;
- `RPY_OPS_TOKEN`.

`OPENAI_API_KEY` is conditional: it is required only while `EMBEDDING_SPACE_RUNTIME_ENABLED=false`, which preserves the historical OpenAI `vector(1536)` retrieval path. With the isolated runtime enabled, BGE is the default self-hosted provider. Cohere is accepted only when the deployment explicitly selects `EMBEDDING_PROVIDER=cohere`, sets `ALLOW_EXTERNAL_EMBEDDINGS=true`, pins `COHERE_EMBEDDING_MODEL=embed-v4.0`, and supplies `COHERE_API_KEY`. Cohere is never an automatic fallback.

Inject secrets from the deployment platform's secret manager or equivalent environment mechanism. Do not place populated values in the repository or bake them into the image. `JUDIT_CALLBACK_URL` is sensitive configuration because its path contains the webhook token; it must be a stable public HTTPS URL whose path exactly matches `/webhooks/judit/<JUDIT_WEBHOOK_TOKEN>`. The production preflight rejects loopback/IP-literal callback hosts, URL credentials, query strings, fragments and non-standard HTTPS ports. `.env.production.example` is a shape-only template. Staging and production must use separate secret sources; do not point both environments at the same PostgreSQL credentials or reuse HTTP/provider secrets between them.

Database credentials are split by responsibility. The five URLs must use distinct PostgreSQL login roles and target the same application database:

- `MIGRATION_DATABASE_URL` is the one-shot deploy/admin credential used by migrations and role provisioning;
- `API_DATABASE_URL` is injected into `api` as its `DATABASE_URL`;
- `WORKER_DATABASE_URL` is injected into both workers as their `DATABASE_URL`;
- `SCHEDULER_DATABASE_URL` is injected into the singleton scheduler as its `DATABASE_URL`;
- `BACKUP_DATABASE_URL` is reserved for backup jobs and role provisioning and is not injected into normal runtime services.

Secrets are scoped by service instead of being copied to the whole stack:

- `api` receives `API_DATABASE_URL` plus Judit, bearer-token and ops credentials;
- `worker-*` receives `WORKER_DATABASE_URL`, Judit/DataJud, Anthropic and embedding/reranker settings; provider credentials stay worker-only;
- `scheduler` receives only `SCHEDULER_DATABASE_URL` and retention/scheduling settings;
- `migrate` receives the migration URL plus the four runtime/backup URLs needed to provision and rotate their roles;
- provider keys must not be present in API, scheduler or migration environments;
- HTTP-facing credentials must not be present in workers, scheduler or migration environments.

If a PostgreSQL password contains reserved URL characters, URL-encode it in the corresponding database URL. Do not reuse the migration/admin role for API, worker, scheduler or backup access.

Bearer-token rotation is performed by temporarily mapping both old and new tokens to the same tenant, deploying that overlap, migrating clients, and then removing the old token in a later deploy.

## Embedding rollout and rollback

The production worker environment always carries the rollout selectors so both workers agree on one semantic space:

- `EMBEDDING_SPACE_RUNTIME_ENABLED` defaults to `false`;
- `EMBEDDING_PROVIDER` defaults to `bge` for the isolated runtime;
- `BGE_EMBEDDING_MODEL` is pinned to `BAAI/bge-m3`;
- `BGE_EMBEDDING_PATH` is the absolute path inside the worker container where the pre-provisioned BGE artifact is available;
- `BGE_EMBEDDING_DEVICE` and `BGE_EMBEDDING_USE_FP16` control local inference characteristics;
- `ALLOW_EXTERNAL_EMBEDDINGS` defaults to `false`;
- `COHERE_EMBEDDING_MODEL` is pinned to `embed-v4.0`;
- `EMBEDDING_MODEL` remains the legacy OpenAI model selector.

When BGE is active, production preflight requires `BGE_EMBEDDING_PATH` to be an absolute container path. This prevents production activation from silently falling back to a model-hub download. The deployment must install the optional embeddings dependencies and either bake or mount the complete BGE artifact at that path before workers start. Runtime model use then fails explicitly if the path is missing or not a directory.

Cohere is an external-data boundary. `ALLOW_EXTERNAL_EMBEDDINGS=true` may be set only after explicit environment-specific approval by the data/governance authority responsible for that deployment. CI and automated tests never authorize real external embeddings. Staging and production require separate approval. See `docs/engineering/external-embedding-authorization.md`.

Secret process versions never cross the Cohere boundary. The normal RAG path short-circuits secret processes, the Cohere historical reindex excludes secret versions, and the embedding runtime independently rejects external embedding of a secret version. A Cohere error does not fall back to BGE, OpenAI or another provider.

Switching BGE ↔ Cohere requires controlled re-embedding/reindexing into the target provider/model space even though both use 1024 dimensions. Old provider/model rows remain available for rollback and are never mixed into the active ranking. Rollback to the legacy path disables the isolated runtime and restores `OPENAI_API_KEY`.

## Reranker activation

Long-process reranking is independently controlled from embeddings and is disabled by default.

- `RERANKER_ENABLED=false` keeps the existing retrieval-selection path without reranking.
- `RERANKER_PROVIDER=bge` selects the default self-hosted `BAAI/bge-reranker-v2-m3`.
- production BGE activation requires an absolute `BGE_RERANKER_PATH` containing the prepared local artifact; `RERANKER_MODEL` remains pinned to the semantic model identity.
- `RERANKER_USE_FP16` controls local inference only.
- `RERANKER_PROVIDER=cohere` selects the optional external Cohere Rerank path.
- Cohere reranking requires a separate `ALLOW_EXTERNAL_RERANKER=true` authorization, `COHERE_RERANKER_MODEL=rerank-v4.0-pro`, and worker-only `COHERE_API_KEY`.
- `RERANKER_TIMEOUT_SECONDS` controls the external reranker timeout.
- no provider is an automatic fallback for the other.

Authorization for external reranking is independent from authorization for external embeddings. Enabling Cohere embeddings does not implicitly authorize Cohere reranking, and vice versa. Secret processes short-circuit before external reranker resolution.

Both workers must receive identical reranker enablement, provider, model, artifact and authorization settings. Production preflight rejects an enabled BGE reranker without an absolute artifact path and rejects Cohere without explicit authorization/key. Before enabling BGE in production, run `scripts/verify_bge_reranker_artifact.py` against the mounted artifact. The real benchmark in `docs/evaluation/reranker-benchmark.md` remains a separate release-quality evidence requirement for #121.

## Optional DataJud enrichment

DataJud is supplementary metadata enrichment and is disabled by default. Production workers carry the complete activation contract so an approved environment does not require a code/Compose edit:

- `DATAJUD_ENABLED=false` by default;
- `DATAJUD_AUTHORIZED_USE=false` is a separate deployment/legal gate;
- `DATAJUD_API_KEY` is worker-only and required only when enrichment is enabled;
- `DATAJUD_BASE_URL` defaults to `https://api-publica.datajud.cnj.jus.br`;
- `DATAJUD_TIMEOUT_SECONDS` defaults to 20 seconds.

Enabling DataJud requires both `DATAJUD_ENABLED=true` and `DATAJUD_AUTHORIZED_USE=true`. Preflight rejects an enabled configuration without explicit authorization, a key, an HTTPS base URL, or a positive numeric timeout. Both workers must receive identical DataJud settings.

This gate is intentional. The current CNJ rules for the public DataJud API require legal, non-commercial and authorized use and attribution to CNJ/DataJud. The technical presence of the adapter or public API key does not itself authorize a deployment. See `docs/engineering/datajud-enrichment.md` and `docs/release/provider-acceptance.md`.

Secret processes are skipped before the DataJud transport call; enrichment failures do not invalidate an otherwise valid Judit version.

## Optional Langfuse observability

The production image includes the pinned Langfuse v4 SDK, but tracing remains disabled by default. Workers receive the optional observability settings; API, scheduler and migration services do not receive the Langfuse secret key.

To enable tracing:

- set `LANGFUSE_ENABLED=true`;
- provide worker-only `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`;
- set an HTTPS `LANGFUSE_BASE_URL`;
- set `LANGFUSE_TRACING_ENVIRONMENT` (default `production`).

Production preflight rejects enabled tracing with missing credentials, a non-HTTPS base URL or an invalid environment name. Both workers must use the same observability configuration. The central log-safety layer redacts `LANGFUSE_SECRET_KEY`.

Langfuse remains fail-open: tracing failures do not decide whether a summary is generated, validated or published. PostgreSQL is the durable application/audit source of truth. Configure and verify the intended Langfuse retention policy separately; self-hosted event data is not automatically expired by default. See `docs/operations/langfuse.md`.

## Deploy sequence

For the supported single-host Compose topology, the canonical operator entrypoint is:

```bash
chmod 600 /secure/path/staging.env
python scripts/deploy_production.py --env-file /secure/path/staging.env
```

The env file must live outside the repository. On POSIX hosts the runner rejects group/world-readable
secret files. It makes values from that file authoritative over inherited Rpy/provider environment
variables, validates both the deploy environment and rendered Compose contract before mutation, pulls
the configured images, runs the declarative Compose startup with health/dependency waiting, and
confirms the host-local `/ready` endpoint.

A no-mutation preflight is available:

```bash
python scripts/deploy_production.py --env-file /secure/path/staging.env --dry-run
```

The runner deliberately does not provision the VM, DNS/TLS ingress, secret manager, off-host backup
destination, or registry credentials. Those remain environment responsibilities.

The underlying deployment sequence is:

1. Build and publish the application image in trusted CI, then record its immutable registry digest.
2. Set `RPY_IMAGE` to that digest and inject the environment-specific configuration and secrets.
3. Run `python scripts/validate_deploy_env.py` against the exported environment, or `python scripts/validate_deploy_env.py --env-file /secure/path/production.env` for a local secret-managed file.
4. Render and validate the compose file with `docker compose -f compose.production.yaml config` and `scripts/validate_production_compose.py`.
5. Pull the exact digest before changing running services.
6. Start PostgreSQL or verify the managed PostgreSQL endpoint is healthy.
7. Run the one-shot `migrate` service to completion using the same `RPY_IMAGE` digest. This applies migrations and provisions/rotates the runtime roles.
8. Reindex into the selected isolated embedding space before switching production retrieval to it.
9. Start the egress proxy, API, both workers, and the singleton scheduler using that digest.
10. Route traffic only after `/ready` succeeds through the TLS-terminating reverse proxy or ingress.

A deploy must stop if preflight, compose validation or migrations fail. Do not start a second scheduler to compensate for scheduler failure; restart or replace the singleton instance instead.

## CI guardrail

CI first runs `scripts/validate_deploy_env.py` with non-secret fixture values, then renders `compose.production.yaml` and runs `scripts/validate_production_compose.py`.

The deploy-environment preflight rejects configuration that:

- uses a mutable image reference instead of a full SHA-256 digest;
- leaves required values empty or at documented placeholder values;
- omits `OPENAI_API_KEY` while the legacy embedding path is selected;
- enables BGE without an absolute `BGE_EMBEDDING_PATH` inside the worker container;
- selects Cohere without `ALLOW_EXTERNAL_EMBEDDINGS=true` and `COHERE_API_KEY`;
- selects an unsupported embedding or reranker provider/model pair;
- enables BGE reranking without an absolute `BGE_RERANKER_PATH`;
- selects Cohere reranking without separate `ALLOW_EXTERNAL_RERANKER=true` authorization and `COHERE_API_KEY`;
- enables DataJud without `DATAJUD_AUTHORIZED_USE=true`, `DATAJUD_API_KEY`, HTTPS base URL and a valid timeout;
- enables Langfuse without credentials, an HTTPS base URL or a valid tracing environment;
- omits a required provider hostname from `EGRESS_PROXY_ALLOWED_HOSTS`, or uses wildcard/IP-literal entries;
- reuses a PostgreSQL login identity across migration/API/worker/scheduler/backup responsibilities;
- points the role-specific URLs at different PostgreSQL databases;
- supplies an invalid bearer-token-to-tenant mapping.

The compose validator rejects changes that:

- use `build:` for any application service;
- use a mutable application image tag instead of a SHA-256 registry digest;
- use different application digests for migration/API/workers/scheduler;
- expose PostgreSQL on a host port;
- remove the internal backend/provider-gateway network isolation or give workers/API a direct egress route;
- remove read-only rootfs, capability dropping, no-new-privileges, hardened tmpfs or CPU/memory/PID ceilings;
- disable Docker seccomp confinement or add privileged/capability escalation;
- give the egress proxy provider/database secrets or allow workers to bypass it;
- change the default API bind away from loopback;
- change the explicit API worker count;
- alter the two-worker / one-scheduler topology;
- remove required service-specific configuration;
- allow the two workers to disagree on embedding, reranker, DataJud or Langfuse activation settings;
- distribute provider or HTTP-facing secrets to unrelated services.

This contract is intentionally small. Platform-specific manifests (Kubernetes, ECS, Nomad, Fly.io, Render, etc.) should reproduce these invariants rather than introduce a second application architecture.
