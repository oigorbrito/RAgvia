# Canonical CNJ database contract

`processes.code` is the durable process identity. The application now normalizes Judit lawsuit codes before staging; this migration adds the same invariant at the database boundary for direct writes.

## Migration

`010_process_code_cnj_contract.sql` adds `processes_code_canonical_cnj_chk` as `NOT VALID`.

New and updated rows must use the canonical form:

`NNNNNNN-DD.AAAA.J.TR.OOOO`

The constraint does not validate CNJ check digits. It enforces representation only.

## Why NOT VALID

Historical rows are not scanned or rewritten during deployment. Existing noncanonical rows remain readable until explicitly remediated, while all new/updated rows are protected immediately.

Before validating the constraint in a future migration, audit historical rows with:

```sql
SELECT id, code
FROM processes
WHERE code !~ '^[0-9]{7}-[0-9]{2}\.[0-9]{4}\.[0-9]\.[0-9]{2}\.[0-9]{4}$';
```
