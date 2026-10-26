# RAGVIA-LIVE-06 — First acquisition custody (26/10/2026)

## Scope

The first paid Judit request must be executed on a persistent, access-restricted
operator machine, **not on an ephemeral GitHub Actions runner**. The
\`provider-live-smoke.yml\` workflow keeps GET-only \`diagnose\` and
\`judit-observe\`, public DataJud and offline modes, but refuses Judit POST
modes \`judit\`, \`judit-roundtrip\`, and \`live\`. The CLI also refuses to
POST in GitHub Actions, even if a temporary file path is supplied.

This procedure does not authorize expenditure. Before any real POST, the
operator must independently establish the public non-secret CNJ, authorization
to obtain that judicial record, account/contract permissions, the approved cost
and expected attachments policy. \`PROVIDER_ACCEPTANCE_AUTHORIZED=true\`
is a technical gate, **not** financial approval.

## Durable boundary and failure contract

Use the existing \`--judit-request-id-file\` option with a PRIVATE path
outside the Git worktree. The implementation:

1. Checks authorization, credentials and disabled paid attachments.
2. Exclusively creates and fsyncs \`<id-file>.attempt.json\` before entering
   the Judit POST. The marker contains only a hash of CNJ, a hash of the
   selected host and a constant state, not the raw CNJ, ID or credentials.
   Competing processes cannot both acquire the same attempt marker.
3. On HTTP success, immediately writes the original ID to \`<id-file>\`
   using exclusive creation and fsync, before the first status/response GET.
   On POSIX, directory entries are fsynced. The file is never overwritten.
4. On a subsequent invocation with the **same path/CNJ/host**, the saved
   ID is reused and only status/response GETs are performed. An isolated
   \`--judit-observe --judit-request-id-file\` is also GET-only.
5. If the attempt marker exists but the ID is absent, incomplete or
   unavailable, the outcome is UNKNOWN. No automatic retry or second POST,
   even after timeout, an HTTP error, interruption, or runner termination.
   Reconcile with Judit support/operator records before any independent
   paid authorization. Do not delete the marker to force a retry.

A crash in the interval between Judit's successful POST and the durable
write cannot magically recover a lost response. This design **blocks the
duplicate charge**, but does not claim the ID is recoverable in that
failure window. A truly unconditional ID guarantee would require a
provider-issued idempotency key or provider-side lookup/reconciliation,
neither of which is validated here.

The file and marker are private operational records. The sanitized stdout
and GitHub captures retain only SHA-256 metadata, never the original ID.

## Windows operator host (PowerShell)

Run inside the RAgvia checkout on the operator-owned Windows machine.
Prepare a non-roaming per-user storage directory **outside the repository**:

~~~powershell
$privateDir = Join-Path $env:LOCALAPPDATA 'RAgvia\private\live06'
New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
& icacls.exe $privateDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" "SYSTEM:(OI)(CI)F"
if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict private custody ACL' }
$requestFile = Join-Path $privateDir 'judit-first-request.id'
~~~

Verify the ACL excludes unrelated users/groups; do not use a shared
workspace, network share, publicly accessible build agent or disposable VM.
Back up the **entire private directory** to an access-controlled durable
backup before any further operational changes; the two files must remain
together. On Windows, Python chmod alone does not replace NTFS ACLs.

Provide \`JUDIT_API_KEY\` through the operator's local secret manager, never
in chat, GitHub inputs, command-line arguments or shell history.
Set \`PROVIDER_ACCEPTANCE_CNJ\` to the actual approved public CNJ by private
operator configuration, and verify the chosen environment:

~~~powershell
$env:JUDIT_REQUESTS_BASE_URL = 'https://requests.prod.judit.io'
$env:JUDIT_ATTACHMENTS_ENABLED = 'false'
python scripts/provider_live_smoke.py --preflight-cnj
~~~

After independent contract/cost authorization, and **once only**, set
\`PROVIDER_ACCEPTANCE_AUTHORIZED=true\` in the private environment and execute:

~~~powershell
python scripts/provider_live_smoke.py --judit-roundtrip --judit-request-id-file $requestFile
~~~

On polling failure or an interrupted process, run the **same command**
again only with its original path/CNJ/host and intact marker; the saved ID
makes it GET-only. Alternatively, use the explicit GET-only operation:

~~~powershell
python scripts/provider_live_smoke.py --judit-observe --judit-request-id-file $requestFile
~~~

If the marker exists but the ID file does not, stop and reconcile with
Judit. Do not rotate the path, change the host/CNJ, or reissue POST to
work around the fail-closed gate.

## Secure handoff to GitHub diagnostics and ingest

After an actual ID is preserved, optionally install it as the private
GitHub Actions secret without printing it:

~~~powershell
Get-Content -Raw -LiteralPath $requestFile | gh secret set PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID --repo oigorbrito/RAgvia
~~~

The operator must verify the secret is configured, with appropriate access
rights; do not post the ID in issues, PRs, logs, URLs or public artifacts.
Only then use \`diagnose\` / \`judit-observe\` (read-only GET).

The request ID alone does **not** prove application ingestion. Continue
using the authenticated callback or captured private provider response,
then check \`tenant_judit_requests\`, \`tenant_processes\` and
\`process_versions.source_request_id\`, with finalized, public case data.
Never create a fresh Judit request to repair ingest. Claude authorization,
PostgreSQL persistence, API render, billing and human legal review remain
separate acceptance gates as defined by LIVE-01. This PR has no live
provider evidence and does not claim LIVE ACCEPTED.
