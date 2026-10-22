# Log-redaction security slice (#472 / #481 / #484)

## Contract and scope

`sanitize_error_message` must remove configured/provider credentials and recognized
credential fields (including the explicit `passcode` alias) while retaining
non-sensitive diagnostic context. Bare `key` and `pass` labels are intentionally
not recognized because they are ambiguous. This slice combines the connection
credential names from #472, webhook/callback fields from
#481, and standalone `rpy_` token recognition from #484. It does not change
logging architecture, authentication, provider requests, DataJud capabilities,
queue behavior, persistence, or the configured-secret environment contract.

Provenance:

- #472: `89a7d1fc7e4bd7c828c0bbb9999b6643cdbd3eb1`
- #481: `fc9d6a03c27b4f3829d81db7a1365531207a314b`
- #484: `914ba705d22d927907c8ee91571560fae1404428`
- Historical source merge-base: `d64cad1a9dbd69e71a5041afe6722c8a3ab3fafb`.
- Integration base after #513: `0d2184f53315ced96953624b58afddfd9ef2d5e8`.

The old source modules are not copied wholesale or merged. #472 and #481 replace
the same key-alternation line and must retain both sets of keys. #484 is
complementary. The literal historical union was rejected in Block250: 11 new
adversarial cases failed despite the existing tests passing.

The reconciled value matcher handles closed quoted values with spaces,
punctuation and escaped quotes. Unquoted `[REDACTED]` markers and any visible
credential tail are consumed as one value; missing quotes render as empty text.
The Authorization matcher covers the seven explicit schemes documented by its
unit cases in flat headers and quoted/nested header representations; it preserves
noncredential diagnostics and does not treat unknown schemes as credentials.
Quoted Digest directive lists are redacted as one header value; an unquoted Digest
list is bounded to that header line so later diagnostic lines remain visible. The
URL matcher, secret configuration, processing order and final output-size bound
remain unchanged.

## Reproduction and evidence limits

Run the existing and new synthetic cases together:

```text
python -m pytest -q tests/test_log_safety.py tests/test_log_safety_redaction_slice.py
```

These are the 57 baseline/adversarial tests qualified in Block250, now run against
the integrated implementation. They cover Authorization, URLs, callback/query
fields, repeated tokens, quoted/escaped secrets, partial masking, negative
controls and preservation of public diagnostic identifiers. Only synthetic
credentials are used. Hypothesis properties do not prove universal scrubbing.

The worker boundary regression also checks the actual rendered runtime log and
its error-detail argument to queue failure handling with unconfigured synthetic
callback/password/rpy values. Diagnostic IDs/status remain, while job identity,
worker ownership, attempts and retry classification are unchanged. This is an
in-process boundary test with a fake queue connection, not a PostgreSQL durability
or restore claim.

New test checks raise explicit failures rather than relying on removable bare
assert statements. A negative control compiling the test with optimization and
a deliberately incorrect sanitizer reproduced the static-analysis finding;
explicit failures retain the check under optimization. No analyzer rule or
workflow was disabled, and no finding suppression is added by this slice.

The helper is not a general PII scrubber or a detector for arbitrary unlabelled
secrets. Malformed/truncated quote grammars, adversarial large-input performance
and all end-to-end logging consumers remain outside this bounded evidence.
Fields explicitly named as credentials are conservatively hidden even when a
caller puts a non-secret status there. Host/status/request IDs outside the
credential value must remain useful for diagnosis.

Block252 execution commands, exact tested commit, JUnit/log hashes and remote CI
are recorded outside the checkout in
`D:/temp/ragv-block252-security-integration/`. Block250's immutable candidate,
failed-union evidence and independently recovered source bundle remain under
`D:/temp/ragv-block250-consolidation/`. Local synthetic results are not live
DataJud/Judit acceptance, a performance claim or approval of another commit.

## Operations and rollback

No schema or configuration migration is required. No live provider, database,
Homologacao198 installation, volume or credential is needed to validate the
sanitizer. Existing DataJud/Judit contract tests must continue to pass without
network/provider access; no functional response or payload mutation is intended.

If a defect requires rollback after a future authorized merge, revert this slice
only. That restores the demonstrated logging gaps and needs an explicit security
decision. Do not roll back DataJud configuration, dependencies or the #513 test
composition. Source PRs and branches stay preserved; neither merge nor closure
of those historical PRs is part of this slice.
