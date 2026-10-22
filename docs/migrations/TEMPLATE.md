# Migration manifest: <capability>

## Goal

Describe the expensive capability being transplanted and why reusing it saves material engineering time.

## Donor

- Repository: `<owner/repo>`
- Commit/ref inspected: `<sha-or-ref>`
- Donor files inspected:
  - `<path>`

## Classification

| Donor element | Decision | Rpy destination | Reason |
|---|---|---|---|
| `<file/function/query>` | `COPY` / `ADAPT` / `REFERENCE_ONLY` / `DROP` | `<path>` | `<reason>` |

## Dependencies introduced

List every new runtime/dev dependency. If none, write `none`.

## Donor concepts removed

List UI, naming, cloud assumptions, generic features, sample code, or abstractions intentionally excluded.

## Rpy adaptations

Describe domain renaming, schema changes, security changes, SQL changes, provider swaps, and any simplification made during integration.

## Invariants to preserve

List behaviors that must remain true after adaptation. Prefer testable statements.

## Tests

- [ ] unit tests added/updated
- [ ] concurrency/behavior tests added where applicable
- [ ] `python scripts/migration_harness.py` passes
- [ ] no donor runtime naming remains
- [ ] no unnecessary dependency remains

## Attribution/license

Record any notice or attribution that must be preserved for this transplant.
