# Next release readiness

**Release version: not yet selected**

This document tracks work after the published `v0.1.1` release. It is intentionally separate from
`docs/release/v0.1.1.md`, which records the release-time scope/evidence for that immutable version.

## Previously recorded repository qualification

The qualification below is a historical result for commit `54835f878e9b7aec7a3a32e2aef896f6e9785a90`, not a current `main` status. On 2026-10-20 this document was reviewed against `origin/main` at `c301acd1e10707791615cbf15493a6f6c707816b`; CI/security status for that newer SHA was not checked here. Preserve the original run records, but do not infer that they qualify later commits:

- CI run `36568990457`: success on the historical candidate;
- vulnerability-scan run `36568990456`: success on the historical candidate;
- CodeQL push run `36568990037`: success on the historical candidate.

Any later code or deployment-contract change creates a new candidate and must repeat exact-head
qualification.

## Technical artifact readiness

| Area | Current evidence | Status |
|---|---|---|
| Project guardrails | `scripts/project_harness.py`, migration/release guardrails | ready |
| Unit/integration | unit + PostgreSQL integration suites in CI | ready |
| Retrieval/generation evals | synthetic/offline evaluation gates | ready |
| Frontend | behavior harness + Chromium smoke | ready |
| Container runtime | image runtime smoke contract | ready |
| Backup/recovery | PostgreSQL backup/restore drill | ready |
| Release smoke | provider-free offline release smoke | ready |
| Production preflight | deploy environment + Compose validators | ready |
| Judit transport | real request/polling/lawsuit response evidence | accepted |
| Judit callback ingress | Repository ingress configuration resolves the API dynamically and has a validator/regression tests (`scripts/validate_staging_ingress.py`, `tests/test_staging_ingress.py`) | repo-side hardening present at the reviewed main SHA; live persistent-staging acceptance must be reverified separately |
| Internal finalization | replay + PostgreSQL E2E + offline Judit-to-summary smoke | ready |
| Production callback config | `JUDIT_CALLBACK_URL` required/validated and injected into workers | ready |
| Persistent staging gate | `.github/workflows/staging-readiness.yml` + staging contract | ready |
| Previously selected immutable image | `ghcr.io/oigorbrito/rpy@sha256:5945b301edde901814579190440f68cd1559bd03defb647fa723e6ff7d2b6ac1` | historical candidate recorded for staging acceptance; current deployment/selection not verified |
| Persistent staging host | local persistent Docker/Compose target with stable HTTPS Funnel ingress and secrets outside Git | provisioned |

“Ready” means the repository has deterministic evidence or a controlled operational procedure.
It does not mean an external provider, legal authority, or production environment has approved
activation.

## Provider acceptance status

### Judit

Basic integration is no longer blocked on generic connectivity or the historical HTTP-400
diagnostic.

Live evidence demonstrates:

- authenticated request creation;
- provider polling and response retrieval;
- real `lawsuit` payload delivery;
- public HTTPS callback initiated by Judit;
- Rpy webhook acceptance, delivery persistence and process-version staging;
- expected tenant access grant;
- zero `application_error` in the recorded public callback acceptance.

See `docs/release/judit-live-callback-acceptance-2026-09-28.md`.

One provider lifecycle detail remains environment-observable rather than a transport blocker: Judit
may deliver lawsuit payloads while the request remains `pending` and send terminal completion later.
The smoke tooling reports this state separately.

### DataJud

DataJud remains optional enrichment behind explicit authorization. Prior live evidence reached the
public API and returned an accepted `not_found` result without a transport/application error. This
proves the API boundary/contract path but does not authorize deployment use or prove that an arbitrary
CNJ will have metadata.

## Environment/activation boundaries

| Boundary | Repository support | Still required outside repository |
|---|---|---|
| Persistent staging | production Compose + readiness workflow + selected immutable digest + stable HTTPS ingress | finish callback/finalization/backup-restore evidence |
| Judit | request/callback/finalization contracts accepted | target-environment credentials/budget and lifecycle observation |
| DataJud | optional enrichment + authorization gate | applicable authorization/current key |
| Anthropic | model routing/retry/validation/secrecy boundary | target key/budget + controlled live acceptance |
| BGE embeddings | artifact/image/reindex tooling | pinned artifact + historical reindex + quality evidence (#124) |
| BGE reranker | verifier + benchmark harness | real artifact/hardware quality/latency evidence (#121) |
| LGPD/governance | technical controls/dossier | legal basis/contracts/retention/erasure approval (#148) |
| iaSummary | canonical output/validation work | residual normative mapping/evidence in #114 |

## Next artifact gate

Before creating the next release tag:

1. select the next SemVer identifier explicitly;
2. update `pyproject.toml` and create matching release notes;
3. freeze an exact release head;
4. run complete CI/security gates on that SHA;
5. publish the exact image through `.github/workflows/image.yml`;
6. record `ghcr.io/oigorbrito/rpy@sha256:...`;
7. smoke-test that exact digest and verify its GitHub artifact attestation;
8. if staging activation is part of the release decision, deploy the same digest to the persistent
   staging environment and run `staging-readiness`;
9. create the immutable tag only after the required release gates are satisfied.

The image workflow is manually dispatchable as well as tag-triggered. Manual publication can be used
to produce a pre-tag staging candidate without moving or inventing a release tag.

## Release vs activation

A software artifact can be repository/offline-qualified while optional provider/model/legal
activation remains pending. Conversely, already-proven live provider connectivity must not be
retested with paid calls merely because a new image is built; repeat only the target-environment
acceptance steps whose behavior could materially differ.

Provider/legal activation follows `docs/release/provider-acceptance.md`, persistent staging follows
`docs/deployment/staging.md`, and production follows `docs/deployment/production.md`.
