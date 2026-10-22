# Contributing to Rpy

Rpy is a small operational RAG service with explicit data-integrity, privacy and recovery constraints. Changes should optimize for demonstrated risk reduction, reproducibility and reviewability rather than abstraction or framework breadth.

Read `AGENTS.md` before making architectural, migration, retrieval, provider, queue or deployment changes.

## Development setup

Recommended local environment:

- Python 3.12;
- Git;
- Docker with Docker Compose;
- PostgreSQL/pgvector through the repository Compose stack.

Install development dependencies:

```bash
python -m pip install pip==26.2.1
python -m pip install --constraint requirements/constraints.txt -e '.[dev]'
```

Run the fast local checks:

```bash
python scripts/project_harness.py
pytest -q tests --ignore=tests/integration
node tests/frontend_behavior_test.mjs
```

Run the PostgreSQL integration suite when changing persistence, queueing, migrations, tenancy, retrieval or recovery:

```bash
pytest -q tests/integration
```

The canonical release-level check is provider-free:

```powershell
.\scripts\smoke_offline.ps1
```

```bash
./scripts/smoke_offline.sh
```

## Change discipline

Keep each pull request focused on one causal story. Describe the failure mode or measurable objective, the invariant at risk, the smallest implementation change and the evidence used to validate it.

Do not mix unrelated refactors with feature or bug-fix work. Avoid speculative abstractions, dependencies and infrastructure. Prefer existing standard-library or repository-local mechanisms when they are sufficient.

For schema changes, use additive/compatible migrations unless a destructive change has an explicit rollout and recovery plan. Never edit an already-applied migration to change historical behavior.

For concurrency or retry behavior, test against PostgreSQL rather than relying only on mocks. For provider boundaries, use fakes in automated tests and prove the exact outbound-data contract.

## Pull requests

Before requesting review:

- rebase or update from current `main` when needed;
- run the relevant local checks;
- run `git diff --check`;
- add regression coverage for bugs;
- update operational documentation when assumptions or runbooks change;
- confirm that no secrets, process payloads or provider credentials were added to code, fixtures, logs or PR text;
- wait for the exact PR head to pass CI before calling the change green.

The GitHub Actions workflow is the source of truth for repository gates. A local pass does not replace a failed or missing CI run. `scripts/project_harness.py` is the repository-level guardrail: it runs the cheap migration/release checks and verifies that behavioral and operational evidence remains wired into CI; it does not replace those stronger gates.

For frontend behavior changes, use `docs/frontend/behavior-harness.md` as the test-design contract: prefer named scenarios and observable effects (requests, rendered state, focus, ARIA state, clipboard and bounded timers) over assertions coupled to implementation structure.

## Commit and naming conventions

Use concise imperative commit subjects. Conventional prefixes such as `fix:`, `test:`, `docs:`, `ci:` and `chore:` are encouraged when they improve scanability, but semantic clarity matters more than rigid formatting.

Use domain names already present in Rpy. Do not preserve donor-project names or abstractions in runtime code unless attribution requires them in documentation.

## Dependencies

New runtime dependencies require a concrete capability that cannot be reasonably implemented with the current stack. Document why the dependency is needed, its runtime/security implications and why a smaller alternative is insufficient.

Do not introduce Redis, Celery, RabbitMQ, BullMQ, Pinecone, LangChain, LlamaIndex or another external vector database without an explicit architecture decision from the project owner.

## Security

Do not report sensitive vulnerabilities in a public issue. Follow `SECURITY.md` for disclosure guidance.
