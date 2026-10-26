# RAGVIA-LIVE-07 — Import of an existing Judit GET snapshot

## Scope and provenance

This tool stages an **operator-supplied completed paginated Judit GET response**.
It does not create a Judit request, call Judit, authenticate a webhook, invent a
callback/completion delivery, fetch attachments, run DataJud, enqueue a worker,
call Claude, or publish a summary.

The previous provider-authored response_type=summary is deliberately ignored.
Only one response_type=lawsuit item is eligible for staging.

Original JSON and request IDs are private. Do not commit the source capture, share
it in GitHub Actions artifacts or put it in a shared folder. Even with
secrecy_level=0 the response contains personal identifiers.

The operator must independently verify the lawful right to reuse the original GET
file and grant it to the exact target RAGVIA tenant. The tool does not
cryptographically authenticate the provider JSON or verify ownership of its account.
Provenance is marked operator_supplied_judit_get_snapshot, not a webhook.

## Read-only preflight (zero network calls)

Run from repository root using Python module form, after installing the project
dependencies. A staging/dry-run does not need PostgreSQL or provider credentials:

~~~powershell
$env:PROVIDER_ACCEPTANCE_CNJ = '6003160-36.2026.8.16.0021'
python -m scripts.import_judit_get_snapshot --file 'C:\PRIVATE\judit-response.json' --cnj $env:PROVIDER_ACCEPTANCE_CNJ
~~~

Expected status: validated_no_database_writes. All IDs and CNJ are SHA-256 hashes.
The tool rejects incomplete pagination, mismatched or invalid CNJ, restricted/unknown
secrecy and private movements/attachments, inconsistent IDs and unexpected pages.

## Optional authorized database staging (no provider calls)

Only once the operator has independently established the correct existing tenant
UUID and secure DATABASE_URL for a migrated RAGVIA PostgreSQL:

~~~powershell
# Supply DATABASE_URL through an existing private secret manager.
python -m scripts.import_judit_get_snapshot --file 'C:\PRIVATE\judit-response.json' --cnj $env:PROVIDER_ACCEPTANCE_CNJ --tenant-id '<authorized-existing-tenant-uuid>' --attest-authorized-reuse --execute
~~~

This is a PostgreSQL write, not a Judit or Claude request. In one transaction,
the tool asserts exact tenant/request/CNJ correlation, stages source_payload
with the original response ID, grants that tenant access and writes a hashed audit
record. A collision with another tenant or a different source is blocked; replay
of the exact same capture is idempotent.

The importer does NOT insert judit_deliveries, judit_request_completions or jobs.
Source-reported completion is kept only in the provenance marker; the tenant
request stays in processing state until a separately approved finalization.
The imported capture is historical and does not prove current tribunal state.

## Separate next gates

1. Verify the source/tenant version in PostgreSQL and the audit record.
2. Prepare controlled finalization with DataJud and attachments disabled.
   The current finalizer can automatically enqueue a generation job. Do not invoke
   that worker as part of this import.
3. Obtain separate financial approval and generation permit for one-shot Claude
   acceptance, then validate persistence, frontend/API parity and legal quality.

Do not re-run judit-roundtrip or initiate any new paid acquisition.
