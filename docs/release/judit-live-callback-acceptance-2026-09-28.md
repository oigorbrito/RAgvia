# Judit live callback acceptance — 2026-09-28

## Scope

This evidence records one bounded live acceptance of the Judit callback path using a public,
non-secret lawsuit lookup with attachments disabled.

The objective was to verify the missing external hop:

```text
Rpy acquisition request
  -> Judit request API
  -> public HTTPS callback
  -> Rpy /webhooks/judit/{token}
  -> delivery persistence
  -> process-version staging
  -> tenant access
```

This was not a production deployment and did not validate production ingress ownership, production
secret management, attachment acquisition, or final summary generation with a live model provider.

## Repository and execution identity

- Base `main` commit: `075b6a6879b73f845a496414073a8615de8c6442`
- Diagnostic branch commit: `15026e02a740c88c3b6ee354364d0cc78d84805e`
- GitHub Actions run: `36499049597`
- GitHub Actions job: `109185298580`
- Provider: Judit
- Attachments: disabled
- Provider request count: one
- Raw CNJ: intentionally not retained in this repository evidence
- Raw Judit request ID: intentionally not retained; only SHA-256 evidence was emitted
- Raw callback URL/token: intentionally not retained
- Raw provider payload: intentionally not retained as an artifact

## Temporary HTTPS ingress

The diagnostic used Cloudflare Quick Tunnel only as short-lived test ingress. The tunnel was created
for the duration of the GitHub Actions job and removed during cleanup. Cloudflare documents Quick
Tunnels as development/testing infrastructure rather than production ingress.

The workflow pinned `cloudflared` `2026.9.3` and verified the official SHA-256 checksum for the
Linux amd64 binary before execution.

The Rpy API ran with access logging disabled so the path-bound webhook token was not emitted by the
HTTP access log. Runtime bearer, ops and webhook tokens were generated per-run and masked by GitHub
Actions.

This ingress pattern must not be promoted as production architecture. Production still requires an
owned TLS-terminating ingress and environment-specific secret management as defined in
`docs/deployment/production.md`.

## Sanitized live result

The provider acquisition was durably registered before callback observation:

```json
{
  "provider_request_registered": true,
  "request_status": "processing",
  "request_id_sha256": "263ed501af44faeefccea8b4425f0813f5d7832f819adca47a9e6107405c251f"
}
```

The callback probe then observed:

```json
{
  "probe": "public_callback_ingestion",
  "delivery_count": 1,
  "response_created_count": 1,
  "lawsuit_delivery_count": 1,
  "application_error_count": 0,
  "process_version_count": 1,
  "tenant_access_count": 1,
  "request_completed_count": 0,
  "request_completion_recorded": 0
}
```

The lawsuit callback arrived and satisfied the probe approximately 15 seconds after the request
correlation step completed.

## Interpretation

This run provides direct live evidence that all of the following worked together:

1. Rpy durably registered an acquisition for the tenant/CNJ.
2. The acquisition worker authenticated to Judit and created the provider request.
3. Judit accepted the supplied HTTPS `callback_url`.
4. Judit initiated an inbound HTTPS callback to the temporary public Rpy endpoint.
5. Rpy authenticated and parsed the callback.
6. Rpy persisted the delivery.
7. Rpy staged a process version from a `lawsuit` response.
8. Rpy granted the expected tenant access.
9. No `application_error` callback was observed.

At the observation point, Judit had not yet sent a `request_completed` event. This is consistent with
the separate live round-trip evidence where lawsuit payloads were already available while Judit still
reported the request as `pending`.

Therefore the remaining provider concern is not generic network connectivity, API-key transport,
request creation, response retrieval, or public callback ingress. The remaining live-provider
uncertainty is the timing/semantics of Judit's terminal completion signal.

## Independent internal-finalization evidence

The repository separately validated the internal completion/finalization path without provider
network calls in diagnostic run `36475647948`:

- replay-tool contract: 3 tests passed;
- webhook/finalization integration path: 8 tests passed;
- full offline Judit-to-summary smoke: PASS;
- summary valid;
- jobs complete;
- provider calls: 0.

Together, the live callback evidence and the offline finalization evidence cover the external ingress
and internal processing halves independently.

## Provider/test references

- Judit requests documentation:
  https://juditdocs.mintlify.app/essentials/requests
- Judit webhook guidance:
  https://judit.io/blog/apis-dados-juridicos-integracoes/webhooks-para-monitoramento-de-processos-judiciais-em-tempo-real/
- Cloudflare Quick Tunnel documentation:
  https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/

## Follow-up

Do not repeat this paid live request merely to reconfirm connectivity.

The next environment-level acceptance should use an owned staging ingress and secret source, then
verify that a real `request_completed` callback drives the already-tested finalization path under the
production deployment topology. If Judit continues to deliver lawsuit payloads while delaying the
terminal completion event, treat that behavior as provider lifecycle/latency evidence rather than as
a transport failure.
