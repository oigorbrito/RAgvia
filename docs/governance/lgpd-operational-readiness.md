# LGPD operational readiness evidence

This document records the technical controls and unresolved governance decisions relevant to issue #148. It is an engineering evidence pack, not a legal opinion and not an authorization to process or index production judicial data.

## Scope

The repository can prove technical behavior. The following decisions remain external to engineering and must be approved by the responsible legal/data-governance authority before #148 can close:

- applicable legal basis for each production processing purpose;
- controller/operator/other-role classification for Rpy, Judit, CNJ/DataJud, Anthropic, Cohere and infrastructure providers under the applicable contracts;
- contractual clauses for data protection, security, retention, subprocessors and incident responsibilities;
- approved retention/erasure periods for process content, summaries, source payloads and audit evidence;
- approved authorization model for portfolio/process ingestion and its documented purpose;
- procedures for revocation, data-subject requests and legally required preservation.

Do not infer any of those decisions from this document.

## Technical data-flow inventory

| Boundary | Data sent or stored | Existing control/evidence | Governance decision still required |
| --- | --- | --- | --- |
| Client → Rpy API | CNJ, authenticated request metadata and public job identifiers | Tenant authentication and CNJ authorization are enforced before acquisition; public job lookup is tenant-scoped. See `docs/engineering/public-api-v1.md`. | Who may authorize each portfolio/process and for which purpose. |
| Rpy → Judit | Authorized process lookup/tracking requests; optional attachment acquisition when explicitly enabled | Provider credentials remain server-side; attachment collection is opt-in; secret processes cannot acquire external attachments. | Contractual role, lawful basis, retention/subprocessing terms and production authorization. |
| Rpy → CNJ/DataJud | Public/authorized judicial metadata when the configured fallback/source path is used | DataJud-derived facts are normalized and provider credentials are not exposed publicly. | Contractual/terms-of-use analysis, lawful basis and permitted production purposes. |
| Rpy worker → Anthropic | Eligible non-secret summary-generation context | Secret processes use a local deterministic path and do not instantiate the external generator. See `docs/engineering/generation-contract.md`. | Legal basis, DPA/contract terms, retention/training commitments and approved environments. |
| Rpy worker → Cohere | Eligible non-secret embedding text only when explicitly selected | External embeddings require `ALLOW_EXTERNAL_EMBEDDINGS=true`; no automatic fallback; secret versions are rejected before external embedding. See `docs/engineering/external-embedding-authorization.md`. | Environment-specific approval, legal basis and provider contractual terms. |
| Rpy → PostgreSQL | Normalized process versions, movements, attachment metadata/chunks, summaries, jobs and audit-related state | Database credentials are role-separated by service; PostgreSQL is not host-published in production. See `docs/deployment/production.md`. | Approved retention/erasure schedule per data class and legally required audit survival. |
| Rpy → public API consumer | Sanitized summary/status/provenance | Public provenance excludes raw source payloads, provider credentials, prompts and raw external request IDs. See `docs/engineering/public-api-v1.md`. | Consumer-facing privacy notice, access purpose and downstream responsibility. |

## Existing minimization and security controls

Engineering evidence currently includes:

- secret proceedings remain on local-only generation paths and are blocked from external embedding paths;
- external Cohere embeddings are disabled unless explicitly authorized per deployment;
- only one embedding provider is active per deployment and semantic spaces are not mixed;
- provider secrets are service-scoped in production and known provider/API credentials are redacted from durable error/log sanitization;
- public provenance deliberately excludes raw source payloads, provider credentials, prompts and raw external request identifiers;
- tests and evaluation datasets are synthetic/provider-free unless an explicit operational validation is performed outside CI;
- tenant/CNJ authorization is applied before public process access or provider-backed acquisition;
- PostgreSQL credentials are split across migration, API, worker, scheduler and backup roles.

These controls reduce exposure but do not establish a legal basis or contractual role.

## Retention state

A complete LGPD retention policy is **not yet defined**.

The repository currently has one narrow, implemented operational retention rule: `judit_request_completions` is purged after `JOB_RETENTION_DAYS`, aligned with terminal queue-job retention. The purge explicitly does **not** delete lawsuit versions, summaries or access-audit records. See `docs/migrations/judit-completion-retention.md`.

Before production mass indexing, the responsible authority must approve a retention matrix covering at least:

| Data class | Current repository status | Required decision |
| --- | --- | --- |
| Raw/provider source payloads | Persisted where required by ingestion/versioning behavior | Retention period, lawful preservation exceptions and erase procedure |
| Normalized movements/process versions | Historical/versioned application data | Retention period and conditions for deletion/anonymization |
| Attachment bytes/chunks/derived text | Processing/indexing data | Retention period, deletion propagation and backup handling |
| Generated summaries | Persisted application output | Retention aligned to source/process lifecycle |
| Queue jobs/completion markers | Partial operational retention exists | Confirm operational window and legal/audit requirements |
| Access/security audit evidence | Must survive some operational cleanup paths | Required survival period, access controls and deletion exceptions |
| Backups | Backup/restore procedure exists | Retention, expiry and erasure propagation policy |

## Production authorization checklist

Before enabling production mass indexing, record approval outside the repository and verify all of the following:

- [ ] legal basis documented for each processing purpose;
- [ ] contractual role of each external participant/provider reviewed and approved;
- [ ] DPA/data-protection, security, retention and subprocessor clauses approved where applicable;
- [ ] portfolio/process authorization source and purpose documented;
- [ ] production environment/provider choices explicitly approved;
- [ ] Cohere remains disabled unless separately approved for that environment;
- [ ] secret-process external-provider prohibitions remain enabled and tested;
- [ ] retention/erasure matrix approved for database, attachments, audit evidence and backups;
- [ ] revocation and data-subject request procedure documented;
- [ ] incident ownership/escalation procedure documented;
- [ ] repository/CI fixtures remain synthetic and contain no production secrets or real process payloads;
- [ ] responsible legal/data-governance approver signs off on this evidence pack and the external contractual documents.

## Machine-checkable approval record

The repository ships `docs/governance/lgpd-approval-record.example.json` as a template and
`scripts/lgpd_governance_readiness.py` as a fail-closed validator for a **private** approval
record. The populated record must not be committed to Git. It stores references and approval
states only; secret-bearing fields are rejected. The validator deliberately reads the record from
standard input rather than opening a caller-supplied path, and CLI output never echoes approval-record
fields or values.

Before mass indexing, the deployment operator can validate the externally approved record with:

```text
Get-Content -Raw <private-approval-record.json> | py -3.12 scripts/lgpd_governance_readiness.py --stdin
```

A successful result proves only that the required governance decisions were explicitly recorded
in the expected machine-readable shape. It does not prove that those decisions are legally
sufficient, does not inspect provider contracts, and does not authorize provider calls by itself.

## Closure boundary for #148

Engineering can maintain this inventory and implement approved technical retention/authorization controls. Issue #148 must remain open until the unresolved legal/organizational items above are decided, the resulting contracts/policies are referenced or versioned appropriately, and the responsible authority explicitly approves the completed package.
