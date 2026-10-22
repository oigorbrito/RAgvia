# Project harness

`scripts/project_harness.py` is the repository-level evidence guardrail for Rpy. It does not replace unit tests, integration tests, evaluation gates, restore drills or smoke tests. Its role is to make the project's verification model executable and auditable.

## Evidence model

Rpy separates three kinds of verification:

1. **Guardrails** — cheap deterministic checks for stable, high-consequence invariants. These belong in the project/migration/release harness.
2. **Behavioral evidence** — executable tests of user-visible or domain behavior. These belong in unit, frontend and PostgreSQL integration suites.
3. **Operational evidence** — drills and runtime probes that exercise built artifacts or recovery paths. These belong in container smoke, backup/restore and offline release smoke.

The project harness executes only the cheap guardrails and verifies that the stronger evidence layers are still wired into the canonical CI workflow and the image publication workflow. It deliberately does not re-run the stronger suites itself.

## What the project harness observes

The harness records named observations with:

- `name`: the invariant/evidence gate being checked;
- `kind`: `guardrail`, `evidence-wiring` or `evidence-contract`;
- `status`: pass/fail;
- `evidence`: the reproducible command or repository artifact;
- `detail`: the observed result.

Current project-level observations include:

- migration/architecture guardrail result;
- release-artifact guardrail result;
- static-quality gate wired in CI;
- unit-test gate wired in CI;
- synthetic RAG evaluation wired in CI;
- offline pipeline and generation evaluations wired in CI;
- frontend behavioral harness wired in CI;
- container runtime smoke wired in CI;
- backup/restore drill wired in CI;
- PostgreSQL integration tests wired in CI;
- offline release smoke wired in CI;
- image/tag workflow runs the project harness, unit tests and PostgreSQL integration before publish;
- published image digest is runtime-smoke-tested after build/push;
- empirical-engineering and contributor contracts present.

This verifies the **existence and connection of evidence**, not the truth of an operational claim. For example, confirming that the restore drill is wired into CI is not evidence of production RTO; the drill result is the evidence for the tested environment.

## Why this design

The harness follows the repository's empirical-engineering policy:

- stable invariants should be cheap and machine-checkable;
- behavioral correctness should be tested at the layer where the failure occurs;
- PostgreSQL semantics should be tested against PostgreSQL;
- provider boundaries should use deterministic fakes in normal CI;
- recovery claims require restore drills, not static configuration;
- performance/reliability claims require measured experiments or production observations;
- a hypothesis must not become a permanent hard gate without evidence.

This avoids two common failure modes: a giant meta-test suite that duplicates all other tests, and a purely textual checklist that silently drifts away from CI.

## Methodology references

The project harness is Rpy-specific. It is not an implementation of SWE-bench or Harbor. The repository uses their public documentation only to calibrate how evidence is identified and reproduced.

Primary references:

- SWE-bench harness: https://www.swebench.com/SWE-bench/reference/harness/
- SWE-bench evaluation guide: https://www.swebench.com/SWE-bench/guides/evaluation/
- Harbor core concepts: https://www.harborframework.com/docs/core-concepts
- Harbor eval execution/results: https://www.harborframework.com/docs/run-jobs/run-evals

The mapping is intentionally narrow:

| External evaluation practice | Rpy mapping |
|---|---|
| Isolated/containerized execution | CI PostgreSQL service, container runtime smoke, offline smoke and restore drill |
| Identifiable evaluation run | exact Git commit + GitHub Actions run id |
| Explicit task/environment/test contract | named CI command, fixture/dataset and execution environment |
| Structured result/log artifacts | evaluator JSON/stdout, pytest/CI logs and drill output |
| Dataset/task separation from agent/runtime | synthetic fixtures and benchmark scripts remain separate from production provider/model evidence |

The harness checks wiring and cheap invariants. It does not turn external benchmark methodology into Rpy score thresholds, and it does not make a synthetic result equivalent to live-provider or production evidence.

## Running

Human-readable evidence:

```bash
python scripts/project_harness.py
```

Machine-readable evidence:

```bash
python scripts/project_harness.py --json
```

A successful local run means the cheap repository guardrails pass and the expected evidence commands remain connected to canonical CI. It does not replace a green CI run on the exact commit.

## Adding or changing a project rule

Before adding a project-harness observation, identify:

1. the concrete failure being prevented;
2. why that failure is high-consequence or likely enough to justify a permanent gate;
3. the strongest local evidence available;
4. why a unit/integration/drill check is not a better enforcement layer;
5. whether the observation is deterministic and cheap;
6. the condition that would make the rule obsolete.

If the rule only asserts that a command remains wired to CI, the underlying command must itself provide meaningful evidence. Do not add ceremony-only gates, arbitrary thresholds, source-line counts, coverage quotas or stylistic checks without a demonstrated Rpy failure mode.
