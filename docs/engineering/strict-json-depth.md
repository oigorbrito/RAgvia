# Strict JSON parser depth failures

## Contract and failure

`loads_strict_json` normalizes a parser `RecursionError` to `ValueError`, the
existing rejection contract used by JSON consumers. The narrow shared helper
is the enforcement layer; endpoint/authentication/queue code is not replaced.
Duplicate-key and non-standard numeric rejection, accepted native structures,
input representations and ordinary malformed JSON behavior remain unchanged.

Before this change a synthetic deeply nested request produced an unhandled
parser error and HTTP 500 at `/v1/resumos` and the token-checked Judit webhook.
Afterward existing handlers return their existing HTTP 400 details, before
summary authorization or webhook database access respectively. This is local
ASGI evidence, not a real Judit delivery or installed-service qualification.
Affected contracts: `API-FASTAPI-001`, `JUDIT-BOUNDARY-001`; tenant/security
checks, DataJud activation and paid acquisition authorization remain canonical.

The DB encoder already treats strict-JSON `ValueError` as a signal to preserve
ambiguous serialized text as a JSON string. Depth-rejected text now follows
that existing fallback instead of raising `RecursionError`. Tests cover its
Python encoding and real PostgreSQL JSONB roundtrip, including a valid native
object control, using parameterized SELECT only in a new disposable database.
No SQL/schema, persistence flow, business data or operational database change.

## Provenance and adaptation

Recovered from the frozen Block249 **G** entry
`security/bounded-json-nesting-failure`, former PR #340:

- Production change: `0c96c19` (full SHA and patch-id in Block253 evidence).
- Source HEAD/test commit: `08775730bb0caf161006357aa56f4b0fbe30edd2`.
- Production helper patch is equivalent to that source; no branch merge or
  unrelated historical code was imported.

The source's 2,000-level fixture is accepted by local Windows Python 3.12.10.
A 4,000-level fixture reproduced 12 failures across parser/input types, HTTP
and PostgreSQL codec boundaries before applying the production fix. Linux
Python 3.12.14 accepts that fixture too, and rejects 16,000 levels instead.
Both failed fixed-fixture attempts and the native parser probes are retained
as portability evidence, not falsely reported as production regressions.

The session fixture now probes standard-library `json.loads` with a bounded
candidate list and chooses an actual `RecursionError` for the current runtime;
JUnit records that selected depth. It does not call the patched helper, alter
recursion settings, skip assertions or create a new production depth policy.
If the parser accepts all candidates, the fixture explicitly fails for review.
These values are a test-input budget, not universal limits or performance
thresholds. Production remains exactly the recovered exception normalization.

Source G classifications are unchanged. No open PR examined touched these
JSON paths; #514 changes separate logging paths and is not a prerequisite.
The source, final diff and experiments retain separate hashes/commit identities.

## Validation and limits

Block253 evidence: `D:/temp/ragv-block253-consolidation/`. It records the exact
commit/diff, commands, package versions, JUnit, network guard and disposable
PostgreSQL image/version. Only directed parser/ASGI/codec/consumer tests run;
the 273 Block252 tests and billing investigation are not repeated. Remote
qualification is assessed on the actual published HEAD, never inherited.

The initial full Windows selection also exposed seven existing Unix-socket
cases unavailable in Windows asyncio. Its 77-pass/7-fail result is retained;
portable Windows and separate disposable Linux execution are reported with
explicit environments, not hidden skips or changes to the existing tests/CI.

The change catches `RecursionError`; it does not establish a fixed JSON depth
policy, bound all parser CPU/memory, catch `MemoryError`, or replace body-size
limits. No benchmark, live-provider, production durability or Homologação198
claim follows from these tests. No credentials/configuration, DataJud or Judit
queries, operational volumes, frontend or installer changes.

Rollback, if authorized, reverts this slice without schema/data migration;
pathological parser inputs would again expose the demonstrated error behavior.
Do not merge until actual remote checks and manual review are qualified.
