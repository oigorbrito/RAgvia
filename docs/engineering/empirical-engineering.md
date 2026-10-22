# Empirical engineering policy

Rpy uses empirical evidence to calibrate engineering policy without pretending that published averages are universal laws. This document separates durable repository invariants from hypotheses and gives agents a reproducible way to turn evidence into changes.

## Evidence hierarchy

Use the strongest evidence available for the decision being made:

1. **Production outcome in Rpy**: incidents, SLI/SLO observations, queue lag, provider latency/error rates, restore duration, data-integrity findings.
2. **Reproducible Rpy experiment**: PostgreSQL integration test, concurrency test, migration/restore drill, benchmark, fault injection, container smoke.
3. **Repository history**: CI failures, review defects, reverted changes, repeated classes of bugs.
4. **External empirical research/systematic review**: useful for identifying likely failure modes, metrics, and experimental designs.
5. **Expert convention**: useful when evidence is unavailable, but must remain a hypothesis until validated locally.

A lower tier can justify a reversible guardrail when the cost is low and the failure consequence is high. It should not justify a precise performance/reliability threshold presented as fact.

## What belongs in the migration harness

The harness should contain cheap, deterministic checks for properties that are both stable and expensive to violate:

- forbidden architectural dependencies;
- queue ownership/idempotency primitives;
- provider/privacy boundaries that must remain present;
- vector dimension/schema coupling;
- finalized-source immutability/idempotency mechanisms;
- immutable production image/dependency-lock mechanisms;
- existence of recovery and engineering documentation.

The harness should **not** enforce subjective style, arbitrary file/PR size, coverage percentage, latency targets, test-count targets, or source-code strings whose presence is not itself a durable contract. Prefer semantic tests or database constraints for behavior.

When a harness rule becomes obsolete, change the rule in the same PR that changes the invariant and document the reason. A harness failure is evidence of contract drift, not proof that the proposed architecture is wrong.

## Testing strategy

Choose tests by failure mode, not by pyramid dogma.

- Pure transformations/validation: unit tests.
- SQL semantics, constraints, transactions, concurrency, retention, tenancy, queue fencing, migrations: real PostgreSQL integration tests.
- External provider request shape: contract tests/fakes at the provider boundary; never require live paid provider calls in normal CI.
- Packaging/runtime imports: container smoke.
- Backup/recovery: restore drill into a separate database with checksum and sentinel verification.
- Deployment topology: static compose contract plus runtime smoke where practical.

A regression fix should normally add a test that fails for the pre-fix behavior. For races, test the interleaving/invariant rather than merely repeating a test many times.

## Database evolution

Schema evolution is treated as an application/data compatibility protocol, not just DDL. The default sequence is:

1. **Expand** with an additive/backward-compatible structure.
2. **Migrate/backfill** in bounded, observable work when data movement is required.
3. **Verify** invariants and compatibility with the old and new application behavior.
4. **Switch** reads/writes to the new representation.
5. **Contract** only after evidence shows the old representation has no live readers/writers and recovery is understood.

Triggers/constraints are appropriate when an invariant must survive application bugs or multiple writers, as with finalized Judit source immutability. Avoid using triggers for business workflow that is clearer and testable in application code.

Every migration should be deterministic, ordered, and exercised by the migration harness/CI. Destructive migrations need an explicit operational reason and recovery path.

## Review and change size

Modern code review is an important quality-control practice, but empirical code-review literature is heterogeneous: studies use many different datasets and metrics. Rpy therefore does not encode a universal maximum PR line count or review duration.

Instead, prefer one causal story per PR:

- a reviewer can state the failure mode being fixed;
- tests demonstrate that failure mode;
- schema/deployment changes are separated when they have independent rollback concerns;
- unrelated formatting/refactors are excluded;
- stacked PRs are acceptable when each head is independently coherent and the dependency is explicit.

Track local signals when enough history exists: escaped defects, rework after review, time-to-green, rollback/revert rate, flaky-test rate, and review latency. Optimize only after a baseline exists.

## Reliability and SLOs

Do not declare an SLO until there is an SLI and a defined measurement point. For each proposed SLO record:

- user-visible behavior being protected;
- numerator/denominator or latency distribution;
- measurement location and exclusions;
- window;
- target and why that target was selected;
- error-budget response;
- known blind spots.

Queue metrics should distinguish backlog size from oldest-job age; provider metrics should distinguish application retries from provider latency/errors; backup monitoring should distinguish backup creation from successful restore. RPO and RTO must come from backup cadence/PITR capability and measured drills respectively.

## Evidence ledger

For material engineering policy, keep this table current. Dates are the dates the evidence was reviewed for Rpy, not necessarily publication dates.

| Area | Evidence | Rpy interpretation | Strength / caveat |
| --- | --- | --- | --- |
| Code review | Davila & Nunes, *Can we benchmark Code Review studies?* (JSS, 2021), systematic mapping of 112 studies; empirical evaluation dominates and hundreds of metrics are used. DOI: `10.1016/j.jss.2021.111009` | Use review as a quality gate, but do not invent universal PR-size/review-time thresholds. Measure Rpy outcomes. | Systematic mapping; demonstrates heterogeneity more than a single best practice. |
| Review automation | Wessel et al., *Quality gatekeepers: investigating the effects of code review bots on pull request activities* (EMSE, 2022), regression-discontinuity analysis across 1,194 GitHub projects plus practitioner interviews. DOI: `10.1007/s10664-022-10130-9` | Automated gates can alter throughput and communication; keep gates deterministic/actionable and avoid noisy rules without demonstrated value. | Large observational/quasi-experimental OSS evidence; not specific to Rpy. |
| Database evolution | Campos et al., *Data Schema Evolution for the Self-Adaptive Software Domain: A Systematic Mapping Study* (Software: Practice and Experience, 2026), 19 studies. DOI: `10.1002/spe.70083` | Treat schema evolution as preservation of data plus non-functional constraints; test migration strategies against application requirements. | Mapping study in a broader domain; supports risk framing, not a specific SQL recipe. |
| Database evolution | *Requirements-driven database evolution: A systematic literature review* (Information and Software Technology, 2026), DOI: `10.1016/j.infsof.2026.108276` | Migration decisions should trace to requirements and reversibility/compatibility concerns rather than schema aesthetics. | Recent systematic review; operational details still require PostgreSQL-specific validation. |
| Reliability | Google SRE Workbook, SLO Document / Error Budget Policy / Monitoring chapters | Define SLOs from measured SLIs, document caveats, use error budgets to balance reliability/change, and connect symptom metrics to diagnostic metrics. | Industry evidence/practice rather than controlled experiment; strong operational precedent, thresholds must be local. |
| Evaluation harness | SWE-bench official evaluation harness and guide: containerized task execution, explicit `run_id`, per-instance reports/logs and test outputs. https://www.swebench.com/SWE-bench/reference/harness/ ; https://www.swebench.com/SWE-bench/guides/evaluation/ | Tie repository evaluation claims to an exact commit/run and preserve structured outputs/logs; prefer isolated execution for behavior that depends on the environment. | External benchmark methodology; it does not define Rpy quality thresholds or imply SWE-bench compatibility. |
| Agent/task evaluation | Harbor official core concepts and eval docs: task = instruction + environment + test script; datasets group tasks; jobs persist config/result/trial artifacts. https://www.harborframework.com/docs/core-concepts ; https://www.harborframework.com/docs/run-jobs/run-evals | Keep task/environment/test contract explicit and distinguish benchmark execution artifacts from application behavior. Synthetic Rpy tasks remain synthetic evidence. | External evaluation framework methodology; no Harbor dependency or score is imported into Rpy. |
| Reliability | Google SRE Workbook, Implementing SLOs, and SRE Book SLO chapters. https://sre.google/workbook/implementing-slos/ ; https://sre.google/sre-book/service-level-objectives/ | Define reliability targets only after the SLI and measurement point exist; keep target selection and error-budget response explicit. | Industry operational practice; target values must be selected and measured locally. |

## Evaluation evidence protocol

For benchmark/evaluation results, repository documentation must distinguish four fields:

1. **Subject under evaluation** — exact commit, image digest, provider/model artifact, or migration set.
2. **Evaluation definition** — command, dataset/fixtures, baseline/contract and test script.
3. **Execution environment** — CI runner/container/service versions and, for performance claims, hardware/model runtime details.
4. **Observed result** — run identifier plus machine-readable report/log or test outcome.

A result is attributable only to the subject/environment that executed. Cancelled, superseded or stale runs are not qualifying evidence.

Synthetic/fake-provider evaluations are valid for deterministic repository contracts, but their scope must remain explicit. They do not establish external-provider acceptance, real ML quality/latency, production throughput/cost, or production recovery objectives.

This structure is consistent with SWE-bench's containerized evaluation plus `run_id`/per-instance artifacts and Harbor's explicit task/environment/test/job-result model. Rpy uses those ideas as methodology references only; no external benchmark score or threshold becomes an Rpy requirement without a separate local contract.

## Current Rpy empirical claims

These claims are supported by repository tests/CI and should remain falsifiable:

- PostgreSQL queue ownership/fencing is validated against a real PostgreSQL service in CI.
- Backup artifacts are checksum-validated and restored into a separate database during CI; this demonstrates recoverability of the tested fixture, not a production RTO.
- Secret-case summary generation is deterministic/local and tests enforce the provider data boundary; this supports a privacy invariant, not a claim of legal sufficiency of the generated text.
- Finalized Judit source data is protected both in application staging behavior and at the database boundary.
- Production application images and base images are immutable by digest, while Python dependency resolution is version-locked. Version locking improves reproducibility but is not equivalent to artifact hash verification or vulnerability freedom.
- Deterministic monetary parsing is shared by publication validation and claim-evidence verification, with property-based checks across equivalent Brazilian numeric/currency representations and explicit malformed-format rejection. This demonstrates parser consistency for the tested formats, not correctness for arbitrary OCR corruption.
- Application error sanitization is property-tested against quoted/unquoted structured credential forms and modern standalone `sk-...` token families. This supports the application/durable-error logging boundary; upstream proxy/CDN/ingress logging remains separate.
- Inbound POST body limiting is property-tested across generated chunk partitions and underdeclared `Content-Length` values, with explicit coverage for public summary/tracking routes. This demonstrates aggregate-byte enforcement in the ASGI middleware, not an upstream transport/proxy body limit.
- Judit webhook path redaction covers valid, malformed, trailing-slash and Unicode-suffix paths while tests verify that malformed paths do not gain authentication state. This supports the application path-logging boundary, not provider callback or ingress behavior.

## How to add a new rule

Before adding a permanent rule to `AGENTS.md` or the harness, answer:

1. What concrete failure does this prevent?
2. What is the consequence if the rule is absent?
3. What evidence says the failure is plausible here?
4. Can a test/constraint enforce the behavior more directly?
5. Is the rule cheap and deterministic?
6. What future change would make the rule obsolete?

If those answers are weak, document a hypothesis and measurement plan instead of adding a hard gate.
