# Block 2: process storage

Rpy stores external process data as immutable-ish source versions and only promotes a version to `processes.current_version_id` when the source request is complete.

## Tables

- `processes`: promoted/current process header and scalar fields.
- `process_versions`: source payload versioning and completion state.
- `process_steps`: one movement per chunk; no overlap.
- `tenant_processes`: authorized portfolio boundary.
- `process_summaries`: generated Markdown plus validation metadata.
- `access_log`: append-only audit log enforced by a database trigger.

## Security invariant

Process reads must join `tenant_processes`. A raw lookup by CPF or unrestricted process search is not part of the storage API.

## Retrieval invariant

Scalar/header data lives on `processes` and is injected directly into prompts. Only `process_steps` participates in vector/lexical retrieval.
