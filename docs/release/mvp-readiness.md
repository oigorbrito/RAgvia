# MVP release readiness

This document separates what is proven in-repository from what still requires an external environment. It is a release gate, not a roadmap.

## Product acceptance

The MVP is acceptable for a provisioned legal user when all of the following hold:

- the user can provide an already-issued tenant bearer token and a CNJ;
- an authorized existing process is readable without provider work;
- knowing a CNJ that exists for another tenant does not grant access to that process;
- an absent or unauthorized process can be requested without exposing the Judit credential or provider request identifier;
- the tenant/CNJ acquisition is durably registered before external provider I/O and provider work runs through the PostgreSQL queue;
- repeated tenant/CNJ requests are idempotent while an acquisition is active;
- a matching Judit lawsuit callback grants access only through the durable tenant request association;
- a callback that arrives before the provider request identifier is persisted is reconciled after that identifier becomes known;
- ambiguous provider failures are not automatically retried, avoiding an unprovable duplicate paid request;
- explicitly rejected requests may be retried only through a later explicit user action;
- process details become readable before summary generation finishes;
- summary generation exposes only the public lifecycle state;
- a validated current-version summary is eventually readable and copyable;
- secret-process content is not sent to external LLM/embedding providers;
- audit, retention and tenant boundaries remain active.

The PostgreSQL integration/E2E suite proves the central requested-CNJ path with external request/generation calls stubbed, while exercising the real API, database, webhook, queue, finalization, tenant grant and summary persistence code. It also covers tenant isolation, callback-before-mapping reconciliation and the safe acquisition failure/recovery contract.

## Repository-proven gates

A candidate is repository-ready only when CI is green for its exact head and covers:

- Project harness, including migration/release guardrails and evidence-wiring checks;
- unit tests and offline evaluation gates;
- production compose/deploy preflight contract;
- frontend behavioral harness plus real Chromium smoke over the checked-in assets;
- local application image runtime smoke;
- backup/restore drill;
- PostgreSQL integration and E2E tests;
- offline provider-free release smoke.

The production contract additionally requires an immutable image digest, split database roles, a singleton scheduler, two queue workers, readiness before traffic, and no provider/HTTP credentials distributed to unrelated services.

Repository readiness does not prove that a provider accepted a real request, that a registry artifact is deployable from the target environment, or that target infrastructure is correctly provisioned.

## External gates before a real user

These cannot be proven by repository CI alone and must be completed in the target environment:

1. Publish the exact candidate image, record its registry SHA-256 digest, smoke-test that digest and verify its GitHub artifact attestation.
2. Provision PostgreSQL 16 + pgvector and distinct migration/API/worker/scheduler/backup credentials.
3. Provision at least one tenant and bearer token through the deployment configuration.
4. Follow `docs/release/provider-acceptance.md` for any real provider boundary: provision a valid Judit API key/webhook and verify one explicitly authorized CNJ acquisition end to end.
5. Exercise one intentionally rejected/invalid provider request only if the provider/environment safely permits it, confirming public failure/recovery behavior without exposing provider internals.
6. Configure only the AI/embedding/reranker providers explicitly selected and authorized for that environment; verify one non-secret fresh process reaches a validated summary and the selected retrieval path behaves as documented.
7. Put TLS ingress/reverse proxy in front of the API, preserve `/health` and `/ready`, and keep the configured webhook body limit.
8. Execute the production deploy sequence using the immutable digest and wait for `/ready` before traffic.
9. Run one target-environment smoke: browser → CNJ request/read → Judit callback → process details → summary publication.
10. Run a backup to the real off-host destination and prove a restore from that stored artifact in a disposable database.
11. Record the deployed digest, migration result, smoke evidence and rollback digest in the release record.

A failed external gate blocks real-user release; it does not justify weakening the repository invariants.

## Explicitly not blocking this MVP

The following are useful later but are not required for the first provisioned-user release:

- interactive login, password reset or self-service tenant provisioning;
- dashboard, recent searches, favorites, alerts or portfolio management;
- React/Next/Vite migration;
- automatic polling beyond the bounded frontend follow-up;
- Kubernetes or another second deployment architecture;
- real-provider calls in CI.

## Release decision

The repository may be classified **repository-ready, external-validation-pending** only when the exact candidate head is green. This means the implemented code paths and repository-operational contracts have passed their automated evidence; it is deliberately narrower than declaring the product production-ready.

A real-user activation remains blocked until every applicable external gate above has evidence from the target environment. In particular, registry publication/attestation verification, real selected-provider behavior, TLS/ingress, target database roles, target-environment browser smoke and off-host restore are not inferred from CI. Legal/governance approval remains a separate prerequisite wherever the selected data boundary requires it.
