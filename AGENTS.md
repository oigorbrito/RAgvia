# RagV agent operating contract

This repository is being assembled rapidly from verified open-source building blocks. The goal is to maximize reuse of expensive infrastructure while preventing unnecessary product identity, dependencies, abstractions, and behavior from entering RagV.

## Engineering decision model

Agents must distinguish **invariants**, **hypotheses**, and **evidence**.

- An invariant is a property whose violation can corrupt data, leak data, duplicate side effects, break recovery, or violate an explicit product contract. Encode stable, machine-checkable invariants in tests, database constraints/triggers, or the appropriate cheap guardrail. `scripts/project_harness.py` is the project-level entrypoint; `scripts/migration_harness.py` and `scripts/release_harness.py` remain specialized guardrails.
- A hypothesis is an engineering belief not yet demonstrated in RagV. Do not turn a hypothesis into a permanent harness rule merely because it is conventional wisdom.
- Evidence is a reproducible observation: a test, CI run, restore drill, benchmark, production metric, incident, or relevant empirical study. Record enough context to reproduce or challenge it.

Prefer repository-local evidence over generic claims. External studies guide risk prioritization and experimental design; they do not prove that a particular threshold or architecture is optimal for RagV. When evidence is weak, say so and choose a reversible change.

The harness is a guardrail, not a second test suite. It should cheaply reject architectural drift and high-consequence invariant violations. Behavioral correctness belongs primarily in unit/integration tests; operational claims belong in drills/metrics.

## Source-of-truth product constraints

RagV is a Python/FastAPI service for judicial-process summarization using PostgreSQL and pgvector. PostgreSQL is also the job queue. The queue must claim work with `FOR UPDATE SKIP LOCKED`. The main runtime services are API, workers, and exactly one scheduler/reclaimer process.

Do not introduce Redis, Celery, RabbitMQ, BullMQ, Pinecone, LangChain, or LlamaIndex as core infrastructure unless the project owner explicitly changes that decision.

## Change protocol

For non-trivial changes:

1. State the failure mode or measurable objective before editing code.
2. Identify the invariant(s) at risk and the narrowest layer that can enforce them.
3. Prefer a small coherent PR with one causal story over a mixed cleanup/feature PR. Small PRs are a reviewability strategy, not a numeric line-count target.
4. Add the lowest-cost test that would have caught the bug. For concurrency, persistence, migrations, SQL semantics, retention, and recovery, include PostgreSQL integration coverage rather than relying on mocks.
5. For schema evolution, preserve compatibility with the currently deployable application during rolling deployment. Prefer additive/expand-migrate-contract changes; destructive contraction requires explicit evidence that old readers/writers are gone and a rollback/recovery plan.
6. For retries and externally repeated events, prove idempotency at the durable boundary. A retry must not mutate finalized historical source data, duplicate irreversible side effects, or renew retention clocks without new activity.
7. For privacy/security boundaries, minimize data before the external boundary and test the exact outbound payload. Secret judicial cases must not call an external LLM.
8. Run the project harness plus the unit, container, migration/restore, frontend, evaluation, and PostgreSQL evidence applicable to the change. Never report green until the actual CI run is green.
9. Update operational or migration documentation when the change alters a durable invariant, deployment assumption, recovery procedure, retention behavior, or external data boundary.
10. Keep rollback explicit. If a migration is intentionally irreversible, document why and how service/data recovery works instead.

## Migration policy

Treat external repositories as component donors, not architectural authorities.

Before transplanting code from another repository:

1. Identify the expensive capability being reused.
2. List the exact donor files/functions/queries required.
3. List transitive dependencies introduced by those files.
4. Classify each donor element as `COPY`, `ADAPT`, `REFERENCE_ONLY`, or `DROP`.
5. Prefer the smallest coherent slice that retains tested behavior.
6. Rename donor-domain concepts to RagV concepts while integrating, not in a later cleanup pass.
7. Remove donor UI, dashboards, marketing, telemetry, deployment assumptions, unrelated APIs, demo tasks, sample data, and provider-specific abstractions unless RagV requires them.
8. Preserve attribution/license files when legally required. Do not remove notices that must remain with copied code.
9. Add or update tests that prove the transplanted behavior in RagV.
10. Run the project harness before considering the transplant complete; use the migration harness directly when diagnosing architectural/migration violations.

## Donor allowlist

Current approved donors and intended scope:

- `ALAN-PSUDO/HydraTask`
  - Allowed: PostgreSQL queue claim pattern, heartbeat, retry/backoff, stale-job reclaim, LISTEN/NOTIFY ideas, graceful worker shutdown, operational queue tests.
  - Drop by default: dashboard/frontend, DAG/dependency features, generic task marketplace/product concepts, webhook product surface, queue administration UI.
- `Azure-Samples/rag-postgres-openai-python`
  - Allowed: PostgreSQL/pgvector retrieval SQL, lexical + vector retrieval, reciprocal-rank fusion patterns, async database integration patterns.
  - Adapt: provider-specific OpenAI/Azure code, English FTS configuration, unsafe/interpolated dynamic filters.
  - Drop by default: Azure infrastructure, frontend, sample domain models, chat-product surface.
- `link178/legal-rag-engine`
  - Reference primarily: retrieval/context/generation separation, evidence/citation verification, evaluation organization.
  - Do not adopt its mock generation provider or its domain abstractions wholesale.

## Explicit denylist for migrations

Reject or remove donor code that introduces any of the following without an explicit RagV requirement:

- donor product names in runtime-facing code;
- donor logos, screenshots, dashboards, marketing copy, demo data;
- Redis/Celery/RabbitMQ/BullMQ;
- external vector databases;
- generic chat-history or conversational-memory subsystems;
- document-upload pipelines unrelated to judicial-process movements;
- generic DAG orchestration;
- browser/frontend code;
- cloud-vendor-specific IaC;
- unused observability stacks;
- dynamic SQL identifiers/operators originating directly from untrusted request values;
- sample tasks, fake providers, mock production paths.

## Required RagV invariants

### Queue

- Claims are atomic and use `FOR UPDATE SKIP LOCKED`.
- No job is concurrently owned by two workers.
- `worker_id` ownership is checked on complete/fail/heartbeat.
- Failed work retries with bounded backoff.
- Stale processing jobs can be reclaimed safely.
- Enqueue supports idempotency for externally repeated events.

### Process/version durability

- A finalized Judit source version is historical evidence and its source fields are immutable.
- Restaging a finalized source is a no-op for source data and must not renew process retention activity.
- Re-finalizing an already finalized version is idempotent: no step rewrite, timestamp renewal, or process mutation.
- Promotion of a version and enqueue of its summary remain one durable transaction.
- A stale/duplicate summary execution cannot degrade an already valid summary.

### Retrieval

- Scalar process metadata is injected directly, not retrieved semantically.
- Short processes can bypass vector retrieval.
- Long processes use lexical + vector retrieval and deterministic post-ranking rules.
- First, last, and recent movements can be force-included independently of semantic ranking.
- High-value judicial milestones can be force-included.

### Generation and validation

- LLM generation is provider-isolated behind a small module with bounded timeouts/retries.
- Validation runs before publication.
- One regeneration attempt is allowed after validation failure.
- A second validation failure is persisted explicitly rather than silently accepted.
- Secret proceedings use the deterministic local summary path and do not instantiate/call an external LLM provider.
- Provider payloads must exclude data outside the documented boundary; logs must never contain prompt/process payloads or secrets.
- Process, movement, attachment and glossary text are untrusted data, never generation instructions; dynamic source/validation content must not be able to close prompt delimiters or redefine the system contract.
- Summary generation is a bounded document-generation surface, not a general chat surface; do not add free-form user instructions, model tools or network access to the generation request without a separate threat model and explicit product decision.
- Public summary generation uses a closed structured-output schema and deterministic application rendering for identity, parties and document structure; do not return final provider-authored Markdown without an explicit security review and adversarial evidence.

### Security, tenancy, audit, and retention

- Tenant/process authorization happens before sensitive process data is returned or sent to an external provider.
- Bearer-token configuration is validated at startup and token comparison is timing-safe.
- Judit webhook secrets are redacted inside the application logging boundary; ingress/proxy redaction is a separate deployment responsibility.
- Webhook bodies are bounded before JSON parsing and deliveries are durably deduplicated.
- Expunge deletes relational and vector process data while immutable access audit survives process deletion.
- Raw Judit delivery payloads have bounded retention.
- Access to judicial processes is auditable without storing secrets or raw provider payloads in the audit trail.

### Deployment and recovery

- Production API, workers, scheduler, and migration job run the same application image digest; mutable application tags/builds are rejected.
- Production base/runtime images are pinned by digest and dependency resolution is version-locked.
- Application-image services use a read-only root filesystem, run as UID/GID 10001, drop all Linux capabilities, enable no-new-privileges, retain Docker seccomp confinement, and have explicit CPU/memory/PID ceilings plus hardened tmpfs for writable temporary state.
- API, scheduler and migration services have no external Docker route; provider-capable workers have no direct egress and may reach external HTTPS destinations only through the credential-free allowlisted egress proxy on the internal provider-gateway network.
- There is exactly one scheduler and at least two workers in the production topology contract.
- Backup is not considered recovery until a restore drill verifies checksum, archive readability, schema migrations, sentinel data, and pgvector in a separate database.
- RPO/RTO are measured operational properties, not promises inferred from configuration.

## Empirical engineering policy

Use empirical literature as a calibration layer:

- Code review research supports treating review as a quality-control activity, but the literature uses heterogeneous datasets and hundreds of metrics; do not invent universal PR-size or review-time thresholds. Optimize for cohesive, independently testable changes and measure RagV's own review/rework outcomes.
- Database-evolution research consistently treats schema evolution as a data-preservation/compatibility problem. Prefer incremental compatible migrations and verify them against the real PostgreSQL engine.
- SRE practice defines SLOs in terms of measured SLIs and error budgets. Do not create reliability percentages from intuition. Document measurement points, caveats, and the evidence behind thresholds; revise them as observations accumulate.
- Automation/bots can change review throughput and communication patterns. Automated gates must therefore explain actionable violations and avoid noisy style policing that has no demonstrated risk reduction.

See `docs/engineering/empirical-engineering.md` for the evidence ledger and how claims are translated into repository policy.

## Evaluation and benchmark evidence protocol

Agents must treat evaluations and benchmarks as reproducible experiments, not narrative claims.

For every result used to justify a change, release candidate, performance statement, or provider/model decision, record enough information to reproduce the observation:

- exact Git commit or immutable artifact digest;
- evaluation command and dataset/fixture identity;
- execution environment relevant to the result (for example container image, PostgreSQL version, model artifact and hardware when applicable);
- run identifier when the execution system provides one;
- machine-readable result or test output;
- pass/fail/metric interpretation and explicit scope limits.

Repository tests and CI results qualify only the commit and environment that actually executed. A later commit inherits no green status automatically.

Synthetic, fake-provider and fixture-based evaluations must be labeled as synthetic evidence. They may demonstrate orchestration, regression invariants, schema/contracts and deterministic quality properties encoded by the fixture. They must not be presented as evidence of live-provider compatibility, real-model quality, production latency, throughput, cost, RTO/RPO, or hardware performance.

Real performance/quality claims require the real implementation under the intended execution conditions. For local ML artifacts, record the exact model/artifact identity and verifier result before using benchmark numbers as deployment evidence.

Evaluation methodology references:

- SWE-bench evaluation harness: https://www.swebench.com/SWE-bench/reference/harness/
- SWE-bench evaluation guide: https://www.swebench.com/SWE-bench/guides/evaluation/
- Harbor core concepts: https://www.harborframework.com/docs/core-concepts
- Harbor eval execution/results: https://www.harborframework.com/docs/run-jobs/run-evals
- Google SRE SLO guidance: https://sre.google/workbook/implementing-slos/

These references support reproducible execution, isolated environments, identifiable runs, structured results, and measured reliability objectives. They do not supply RagV-specific acceptance thresholds. RagV thresholds must come from an explicit product/SLO contract or from locally measured evidence with the measurement method documented.

## Definition of done for a transplant or architectural change

A change is complete only when:

- donor-specific names are absent from runtime code unless present only in attribution/docs;
- no denied dependency was introduced;
- imported files have an explicit reason to exist;
- the relevant expensive/risky behavior is covered at the appropriate test level;
- `python scripts/project_harness.py` passes (including its migration/release guardrails);
- migrations are compatible/recoverable according to the change protocol;
- durable invariants and operational consequences are documented;
- the actual CI head is green;
- the code fits RagV's current module boundaries rather than preserving donor folder structure for convenience.

## Preservação de contratos comprovados

Antes de editar, identifique os contratos afetados no [registro de funcionalidades](docs/engineering/feature-registry.md) e execute a validação correspondente na [matriz](docs/engineering/validation-matrix.md). Mudanças em contratos protegidos exigem uma justificativa técnica, evidência comparativa quando substituírem um mecanismo funcional e um diff restrito ao escopo; não descarte mudanças não commitadas.

Não sobrescreva configuração persistente com defaults durante bootstrap, reinstalação ou atualização. Não reintroduza autenticação removida por decisão explícita. Diferencie bloqueio de autorização de falha técnica e não declare PASS apenas por testes unitários quando o contrato atravessar processos, Docker, PostgreSQL ou um provedor externo.

A homologação não pode modificar bancos, tenants, volumes ou configuração da instalação habitual. Não execute consultas Judit pagas nem consultas DataJud sem autorização específica. Não faça merge, release ou publicação automática. Atualizadores Windows devem rejeitar artefatos sem assinatura Authenticode válida, manifesto confiável, hash compatível e plano de rollback; SHA-256 isolado não prova autenticidade. As regras detalhadas de impacto, evidência e lacunas estão em [regression-policy.md](docs/engineering/regression-policy.md).

## Working style for coding agents

Prefer working behavior over speculative architecture. Make small vertical slices executable early. When an external implementation is already correct and generic, adapt it rather than rewriting it for stylistic reasons. When donor abstractions exceed RagV's needs, delete them aggressively.

Do not optimize for number of commits, lines changed, test count, or coverage percentage in isolation. Optimize for demonstrated risk reduction, reversibility, reproducibility, privacy, and recovery. When a claim cannot yet be demonstrated, leave it as a documented hypothesis with a measurement plan rather than encoding false certainty in the harness.
