# Provider acceptance runbook

This runbook defines the controlled, environment-specific acceptance that happens **after** the repository/offline release gates are green and **before** a deployment enables paid/external provider behavior with real credentials.

It does not grant legal authority, approve data processing, or replace provider contracts. It exists so the product can be delivered ready to receive the approvals, API keys and artifacts that are controlled outside the repository.

## Current contract snapshot

Externally revalidated on 2026-09-18 against official provider documentation:

| Boundary | Rpy contract | Official source checked |
|---|---|---|
| Anthropic generation | `claude-sonnet-5` for <=100 movements; `claude-opus-5` for >100 | https://docs.anthropic.com/en/docs/about-claude/model-deprecations |
| OpenAI legacy embeddings | `text-embedding-3-small`, explicitly requested at 1536 dimensions | https://platform.openai.com/docs/models |
| Cohere embeddings | `embed-v4.0`, float embeddings, explicit `output_dimension=1024` | https://docs.cohere.com/docs/cohere-embed |
| Cohere reranking | `rerank-v4.0-pro`, Rerank v2 | https://docs.cohere.com/docs/rerank |
| Judit auth/services | `api-key`; Requests, Tracking and Lawsuits production services | https://docs.judit.io/llms.txt |
| CNJ/DataJud enrichment | public API under explicit authorized-use gate; secret processes skipped | https://www.cnj.jus.br/sistemas/datajud/api-publica/ and Portaria CNJ 160/2020 as amended by 374/2026 |
| Image provenance | GitHub artifact attestation bound to the published digest | https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations |

The repository tests prove the request shapes with fakes and enforce these configured identifiers. This table is not a substitute for a live acceptance against the account/plan actually provisioned to the target environment.

## Credential-free readiness gate

Before any secret is provisioned, run:

```bash
python scripts/provider_acceptance_readiness.py --json
```

This check performs no network calls and requires no credentials. It validates the repository's
activation posture rather than provider availability:

- Judit and Anthropic are reported as `ready_to_provision` only when their production template
  fields remain empty or explicit placeholders and the live acceptance contract is present;
- DataJud and Cohere remain disabled until their separate authorization gates are explicitly
  changed in the target environment;
- paid Judit attachments remain disabled;
- BGE is reported as pending real artifact/hardware evidence, not as a credentialed provider;
- the Anthropic adversarial workflow must remain manually dispatchable and must skip cleanly when
  `ANTHROPIC_API_KEY` is absent.

A green readiness result is **not** live-provider acceptance. It only proves that the repository is
safe to hand off for later secret provisioning and controlled execution.

### CNJ preflight (zero network)

Before any provider diagnostic or paid request, validate the exact `PROVIDER_ACCEPTANCE_CNJ`
locally:

```bash
python scripts/provider_live_smoke.py --preflight-cnj
```

GitHub Actions exposes the same check as `mode=preflight`. It performs no provider network calls,
does not require provider credentials, and never prints the raw CNJ. The report exposes only:
format validity, CNJ check-digit validity, digit count, input form, justice code and tribunal code.

For controlled tests with an explicitly public/non-sensitive process, workflow dispatch also accepts
the optional `public_cnj` input. When present, it overrides the repository secret
`PROVIDER_ACCEPTANCE_CNJ` for that run. GitHub workflow inputs are visible in Actions metadata, so
never place a private, secret, sealed, tenant-confidential or otherwise sensitive process number
there. All provider-bearing modes (`judit`, `datajud`, `live`, and `replay`) now execute the
same zero-network preflight first and are blocked automatically if the selected CNJ is invalid.

A result other than `status=ok` blocks every provider smoke for that CNJ until the secret is
corrected. This preflight exists specifically to prevent repeated host/payload experiments when the
test identifier itself is invalid.

### Judit non-creating credential diagnostic

Before any paid `POST /requests`, validate the provisioned Judit key with the provider-documented
connectivity check:

```bash
python scripts/provider_live_smoke.py --diagnose-judit
```

The check performs only the provider-documented `GET /requests?page=1&page_size=1`; it does not create a lawsuit request. The requests base URL is constrained to Judit's documented `requests.prod.judit.io` host or the `requests.prod.judit.io` compatibility alias observed in Judit's own examples and public integrations. GitHub Actions `mode=diagnose` probes both hosts independently, without any `POST /requests`, so host/account routing can be distinguished from payload or billing behavior.

The diagnostic records only safe classifications such as `http_401`, `http_403`, `http_429`,
`http_5xx`, `transport_error` or `invalid_response`; when the provider returns structured validation data, the diagnostic may expose only a short machine-safe `provider_error_code` plus allowlisted `field`/`rule` pairs from `error.data`. Free-form validation messages, raw provider response bodies and credentials are never logged. Live combined acceptance also performs this connectivity check before the paid Judit
request. If it fails, the paid Judit request and DataJud smoke are both blocked.

Operational interpretation follows Judit's published authentication guidance:
- 401: missing/invalid/expired key, malformed `api-key` header, or account usage limit condition;
- 403: valid key without permission for the resource or feature unavailable in the contracted plan;
- 429: rate-limit condition; do not immediately repeat a paid acceptance run;
- transport/5xx: treat as provider/network availability and do not infer credential invalidity.

Judit documents that every submitted process request is accounted/billed according to contract even
when the result is served from cache, so diagnostic GETs should be used before repeating a paid POST.

### Controlled Judit-only smoke

After the non-creating diagnostic has isolated host/auth behavior, a single explicitly authorized
paid Judit acceptance call can be run independently of DataJud with GitHub Actions `mode=judit`.
The workflow requires only the authorized CNJ, `PROVIDER_ACCEPTANCE_AUTHORIZED=true`,
`JUDIT_API_KEY`, and keeps `JUDIT_ATTACHMENTS_ENABLED=false`.

The request contract is pinned to Judit's current official CNJ body: `search.search_type=lawsuit_cnj`,
`search.search_key=<CNJ>`, and `with_attachments=false`. The official request example does not
require clients to send `response_type` for this CNJ request; Judit returns `response_type=lawsuit`
in the created request/response objects. The workflow defaults to Judit's currently documented `requests.prod.judit.io` host and keeps
`requests.production.judit.io` only as a compatibility alias observed in earlier successful runs.
Use exactly one host per paid run; do not probe both with POST requests.

A successful run must return `request_created`. Stdout and the acceptance capture store only the
sanitized request-id hash and timing metadata. For live requests, persist the raw Judit request ID
separately so the same paid request can be observed if webhook delivery fails:

```bash
python scripts/provider_live_smoke.py --provider judit \
  --judit-request-id-file /private-artifacts/judit-request-id
```

The raw ID file is written immediately after request creation, before any follow-up observation. On
POSIX filesystems it is forced to mode `0600`; on Windows, access is governed by the containing
filesystem ACL rather than POSIX mode bits, so use a directory restricted to the operator/service
account. Keep that path on a private mounted volume outside the repository. The equivalent
environment variable is `PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID_FILE`. Do not upload this file as an
Actions artifact or include it in acceptance logs.

A production acquisition should prefer webhook completion over long-lived polling. When
`JUDIT_CALLBACK_URL` is configured, Rpy includes it in the Judit request payload. The value must be
an absolute HTTPS URL without URL credentials, query parameters or fragments. The webhook token may
remain in the path because the existing Rpy receiver is `/webhooks/judit/{token}`; never print or
persist the configured callback URL in acceptance logs.

For environments without a public callback endpoint, do not repeatedly create paid requests just to
extend polling. GitHub Actions `mode=judit-observe` performs GET-only observation of an existing
request using `PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID` from repository secrets. This mode performs
zero provider POSTs and records only the SHA-256 of the request identifier plus sanitized status/count
metadata.

Round-trip reporting distinguishes transport/data evidence from provider completion. If one or more
`lawsuit` payloads are already available while Judit still reports `request_status=pending`, the
smoke reports `roundtrip_payload_received_pending_completion`. That state proves the request was
accepted and process payloads were received, but it remains a non-zero acceptance result until Judit
reports completion. Do not classify it as a transport/authentication failure, and do not treat it as
full provider acceptance either. Application errors and completed-without-process outcomes are also
reported separately so operators do not collapse distinct provider states into a generic
`roundtrip_incomplete`.

### Controlled DataJud-only smoke

While Judit acceptance is unresolved, DataJud can be validated independently without loading or
calling the Judit boundary.

GitHub Actions exposes `mode=datajud`, which requires only:

```text
PROVIDER_ACCEPTANCE_CNJ
PROVIDER_ACCEPTANCE_AUTHORIZED=true
DATAJUD_AUTHORIZED_USE=true
DATAJUD_API_KEY
```

The run executes:

```bash
python scripts/provider_live_smoke.py --provider datajud
```

It does not read `JUDIT_API_KEY`, does not run the Judit diagnostic, and does not submit any Judit
request. A DataJud result of `ok` or `not_found` proves the public API boundary is reachable and the
request contract is accepted; `auth_error` or `unavailable` remains a failed provider acceptance.

The current CNJ documentation publishes the API Pública key in the DataJud Wiki and specifies the
request header as `Authorization: APIKey <public-key>`. Because CNJ may rotate that public key at any
time, keep the current value in the environment/secret rather than hard-coding it in the repository.

### Controlled Judit/DataJud smoke

The repository also provides a manual live smoke entrypoint:

```bash
python scripts/provider_live_smoke.py --provider both
```

The CNJ comes from `PROVIDER_ACCEPTANCE_CNJ` unless `--cnj` is supplied. A real network call
requires `PROVIDER_ACCEPTANCE_AUTHORIZED=true` plus the provider-specific credentials and gates.
Without those values the command returns a machine-readable skipped state and performs no network
call.

The workflow `.github/workflows/provider-live-smoke.yml` is manually dispatchable and follows the
same rule. It reads the authorized CNJ and **both** provider credentials from GitHub Secrets, keeps
Judit attachments disabled, and exits with a notice rather than claiming acceptance when provisioning
is incomplete.

The combined path remains available when both providers are ready, but Judit and DataJud may also be
accepted independently so one unresolved provider does not force an unnecessary call to the other.
Each live mode remains bounded to one provider request path per execution and must use an explicitly
authorized CNJ. This smoke does not replace end-to-end webhook acceptance, attachment acceptance,
mass indexing, or legal/governance approval.

### Capture and replay

A successful live smoke can write a sanitized capture:

```bash
python scripts/provider_live_smoke.py --provider both \
  --capture-file provider-acceptance-artifacts/provider-smoke-capture.json
```

The capture stores the CNJ only as SHA-256, records the request shape without credentials, and stores
the sanitized smoke response (status, latency, request-id hash and DataJud result metadata flags).
It does **not** persist API keys, the raw CNJ, raw judicial payloads, or the raw Judit request ID.
When `--judit-request-id-file` is explicitly supplied, the raw request ID is written only to that
separate private file (mode `0600` on POSIX; protected by the containing directory ACL on Windows);
it is never added to the sanitized capture.

The same capture can then be replayed without provider credentials or provider network calls:

```bash
python scripts/provider_live_smoke.py --provider both \
  --cnj "<same-authorized-cnj>" \
  --replay-file provider-acceptance-artifacts/provider-smoke-capture.json
```

Replay verifies that the supplied CNJ hashes to the same identity as the capture, sets
`network_calls_performed=false`, and marks the result with `replayed=true`.

The GitHub workflow exposes `mode=live|replay`. Live mode uploads the sanitized capture as the
`provider-acceptance-capture` Actions artifact with 90-day retention. Replay mode accepts the prior
workflow run ID, downloads that artifact, and performs no Judit/DataJud call.

This capture replays the **acceptance smoke contract**, not a complete Judit lawsuit. Judit request
creation is asynchronous: the immediate response is a request identifier.

For finalized process reuse, Rpy already retains recent webhook envelopes temporarily in
`judit_deliveries.raw_payload` (bounded by the repository's delivery-retention policy). Export a
sanitized replay bundle from that controlled store:

```bash
python scripts/export_judit_replay.py \
  --cnj "<same-authorized-cnj>" \
  --output private-artifacts/judit-webhook-replay.json
```

The exporter resolves the most recent Judit request for the CNJ, reads its callback sequence, replaces
provider request/response/callback identifiers with stable hashes, redacts direct document/contact
fields, pseudonymizes party-name fields, and writes the ordered webhook sequence. The output must stay
outside the repository unless separately reviewed as synthetic-safe.

The bundle can then be applied to a local or staging Rpy environment without calling Judit:

```bash
python scripts/replay_judit_webhooks.py \
  --bundle private-artifacts/judit-webhook-replay.json \
  --cnj "<same-authorized-cnj>" \
  --base-url "http://localhost:8000"
```

`JUDIT_WEBHOOK_TOKEN` is used only to authenticate to the Rpy webhook endpoint. The replay command
does not need `JUDIT_API_KEY` and reports `provider_network_calls_performed=false`.

This gives two reusable layers: the GitHub smoke capture proves provider connectivity/contract, while
the database-exported webhook bundle exercises Judit ingestion and finalization repeatedly without a
new paid provider request.

## Preconditions

Do not start live provider acceptance until all applicable items below are satisfied:

- an immutable `RPY_IMAGE=...@sha256:...` has passed repository CI and published-image runtime smoke;
- its GitHub artifact attestation has been verified;
- `python scripts/validate_deploy_env.py --env-file <secret-managed-env>` passes;
- the target environment has dedicated, rotatable credentials from its secret manager;
- provider budget/rate-limit ownership is known;
- a tenant and process/CNJ explicitly authorized for this acceptance are identified;
- no real process payload is copied into repository fixtures, issues or PRs;
- external embedding/reranking authorization has been granted separately for that environment when Cohere is selected;
- legal/governance prerequisites required by #148 are approved before mass indexing or production-scale processing.

Record the approval reference outside the repository when it contains contractual, personal or confidential information. In repository evidence, record only a non-sensitive reference identifier.

## Evidence record

For each acceptance run, record:

- date/time and environment;
- exact Git commit and application image digest;
- provider and model selector;
- non-sensitive approval/reference ID;
- provider request/tracking ID when safe to retain;
- HTTP/result class, not raw provider response bodies;
- observed latency and the measurement point;
- provider-reported usage/cost when available;
- retry count;
- final application state;
- CI/job/run identifier or equivalent execution identifier;
- sanitized log/report reference;
- operator/reviewer identity according to the organization's normal change-management system.

Never record API keys, bearer tokens, webhook tokens, signed attachment URLs, raw judicial payloads or unredacted personal identifiers.

## 1. Judit acquisition

Start with attachments disabled:

```text
JUDIT_ATTACHMENTS_ENABLED=false
```

Acceptance must prove:

1. one explicitly authorized CNJ can create an asynchronous lawsuit request;
2. the returned `request_id` is durably correlated before any callback can grant tenant access;
3. webhook delivery/finalization reaches the expected tenant-scoped state;
4. a repeated user request does not create an unintended duplicate side effect;
5. cached and fresh responses preserve the documented precedence (`cached_response=false` wins);
6. provider errors are sanitized and do not expose response bodies or credentials;
7. observed request volume/cost is within the pre-approved acceptance budget.

The current Judit documentation index states that API calls use `api-key`, that asynchronous requests use `requests.prod.judit.io`, tracking uses `tracking.production.judit.io`, and lawsuit/attachment access is under `lawsuits.production.judit.io`.

### Attachment activation hold

Keep paid attachment acquisition disabled until a live, explicitly authorized acceptance confirms the attachment contract for the provisioned Judit account.

The official documentation currently has an inconsistency: the canonical documentation index describes the Lawsuits service and `api-key` authentication, while one generated attachment-reference page shows a different host/auth presentation. Because attachment collection is opt-in and chargeable, Rpy must not guess between those representations.

Before setting:

```text
JUDIT_ATTACHMENTS_ENABLED=true
JUDIT_ATTACHMENT_DOWNLOAD_MODE=direct_api_key
```

confirm, with the provider/account actually provisioned:

- accepted authentication header;
- effective attachment endpoint/host;
- whether the first response is bytes or metadata containing a download URL;
- content-type behavior;
- maximum expected size;
- billing behavior;
- handling of private/secret attachments.

If the live contract differs from `app/judit_client.py`, update the adapter and fake contract tests first; do not patch production configuration around a code mismatch.

### Optional DataJud enrichment

Treat DataJud as a separate authorization boundary from Judit. Before enabling it in a target environment:

- confirm the intended use is permitted under the applicable CNJ/DataJud terms;
- record the non-sensitive approval/reference ID;
- set `DATAJUD_ENABLED=true` only together with `DATAJUD_AUTHORIZED_USE=true`;
- inject the current `DATAJUD_API_KEY` only into workers;
- keep the official HTTPS base URL unless an explicitly reviewed CNJ endpoint change requires otherwise;
- verify one authorized, non-secret CNJ lookup;
- verify a secret-process control path performs no DataJud network call;
- confirm normalized provenance/metadata only is persisted and raw DataJud payloads are not stored.

A DataJud auth failure or service outage is supplementary-enrichment failure, not permission to weaken the authorization gate or substitute unreviewed data sources.

## 2. Anthropic generation

Use one explicitly authorized, non-secret process.

Acceptance must prove:

- <=100 persisted movements selects `claude-sonnet-5`;
- >100 selects `claude-opus-5` when that scenario is intentionally exercised;
- the request uses the configured 4,000-token output ceiling;
- the request uses Anthropic Structured Outputs with the closed Rpy JSON Schema and no model tools;
- the application, not the model, renders process identity, parties and Markdown headings;
- generated output passes the post-generation validator before publication;
- one correction attempt behaves as documented when the first output is invalid;
- usage/cache telemetry is persisted without provider secrets;
- a secret-process control case does not call Anthropic at all;
- the live prompt-injection suite passes for the exact candidate prompt/model pair.

Run the bounded adversarial suite with the target Anthropic credential:

```bash
python scripts/evaluate_prompt_injection_live.py
```

The suite injects off-task instructions through process-source text rather than through a nonexistent free-form summary chat input. It covers recipe generation, current-weather diversion, system-prompt exfiltration, delimiter breakout, Base64 obfuscation, multilingual override, hidden markup and payload splitting across movements. A failed case is a provider-acceptance blocker; do not whitelist the attack phrase or weaken the validator to make the suite green. Re-run this suite whenever the summary prompt version or generation model changes.

Do not use a production secret process as a negative test. The repository already proves that boundary deterministically; production acceptance should verify configuration/logging, not expose restricted content.

## 3. Embeddings

Exactly one embedding space is active per deployment.

### Legacy OpenAI rollback path

If `EMBEDDING_SPACE_RUNTIME_ENABLED=false`, acceptance may verify the historical OpenAI path:

- `text-embedding-3-small`;
- explicit 1536 dimensions;
- correct vector count/dimensions;
- retrieval succeeds for an authorized long process.

This path is a rollback/legacy boundary, not the target BGE rollout.

### BGE isolated runtime

Before activation:

- mount the real `BAAI/bge-m3` artifact at `BGE_EMBEDDING_PATH`;
- verify the BGE image/artifact contract;
- perform the controlled historical reindex;
- collect real retrieval-quality evidence required by #124.

No model-hub download during production activation counts as artifact readiness.

### Cohere Embed v4

Only after environment-specific external-data authorization:

- set `EMBEDDING_PROVIDER=cohere`;
- set `ALLOW_EXTERNAL_EMBEDDINGS=true`;
- use `COHERE_EMBEDDING_MODEL=embed-v4.0`;
- provide worker-only `COHERE_API_KEY`.

Rpy explicitly requests 1024 dimensions because Cohere Embed v4 supports multiple dimensions and its provider default is not the Rpy semantic-space contract. Verify returned dimensions and confirm secret versions never cross the external boundary.

## 4. Reranking

Reranking remains disabled until intentionally activated.

### BGE reranker

Before production enablement:

1. mount the real `BAAI/bge-reranker-v2-m3` artifact at `BGE_RERANKER_PATH`;
2. run `python scripts/verify_bge_reranker_artifact.py --model-dir "$BGE_RERANKER_PATH"`;
3. execute `python scripts/benchmark_reranker.py --scorer bge` on the prepared hardware;
4. record observed quality and latency evidence.

Record the exact Git commit, verified local artifact path/identity, FlagEmbedding/runtime version, hardware/runtime environment, benchmark command, machine-readable report and execution/run identifier. The synthetic scorer result is not a substitute for this evidence.

This is the remaining objective acceptance blocker for #121.

### Cohere Rerank

Only after separate external-reranker authorization:

- `RERANKER_ENABLED=true`;
- `RERANKER_PROVIDER=cohere`;
- `ALLOW_EXTERNAL_RERANKER=true`;
- `COHERE_RERANKER_MODEL=rerank-v4.0-pro`;
- worker-only `COHERE_API_KEY`.

A Cohere reranker acceptance does not substitute for the real BGE benchmark required by the product decision in #121.

## Evaluation methodology references

Provider/model acceptance uses Rpy-specific contracts, but the evidence record follows established evaluation structure:

- SWE-bench official harness/evaluation documentation uses isolated execution, explicit run identifiers and persisted result/test logs: https://www.swebench.com/SWE-bench/reference/harness/ and https://www.swebench.com/SWE-bench/guides/evaluation/
- Harbor models evaluation as explicit tasks/datasets/environments with stored job/trial configs and results: https://www.harborframework.com/docs/core-concepts and https://www.harborframework.com/docs/run-jobs/run-evals
- Google SRE guidance treats SLOs as measured objectives built from defined SLIs and measurement points: https://sre.google/workbook/implementing-slos/

These sources inform evidence structure only. Provider acceptance criteria, legal authorization and performance thresholds remain Rpy/environment-specific.

## Stop conditions

Stop the acceptance immediately if any of these occur:

- provider/model differs from the pinned contract;
- a credential appears in logs or error text;
- a secret process reaches an external provider;
- tenant authorization cannot be demonstrated before acquisition/read;
- provider response shape differs from the tested adapter contract;
- retries create duplicate irreversible side effects;
- returned embedding dimensions differ from the configured semantic space;
- provider cost/rate usage exceeds the approved acceptance budget;
- legal/governance approval is absent for the tested data boundary.

A stopped acceptance is evidence of a blocker. Do not work around it by weakening validation or bypassing preflight.

## Release interpretation

The repository/offline release may be technically qualified while live provider acceptance remains pending. That means the software artifact is ready to receive environment-specific secrets and approvals; it does **not** mean the organization is authorized to process real portfolios or enable every external provider.

Production-scale operation remains conditional on the relevant approvals, artifacts and provider acceptance evidence.


### Scheduled adversarial sampling

The repository also defines `.github/workflows/adversarial-live.yml`, scheduled weekly and manually dispatchable. It uses only the synthetic regression corpus in `scripts/adversarial_summary_corpus.json`, applies bounded deterministic mutations, and runs `scripts/evaluate_prompt_injection_live.py`.

A run is live provider evidence only when `ANTHROPIC_API_KEY` is actually provisioned. If the credential is absent, the workflow emits an explicit notice and exits without claiming provider acceptance.

The JSON report identifies `prompt_version`, `model`, individual cases, attack families and aggregate family pass rates. Any live failure remains a provider-acceptance blocker until triaged or explicitly risk-accepted outside the repository. Never replace the synthetic corpus with real process data.
