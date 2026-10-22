# RagV engineering handbook

This directory records engineering policy that spans individual migrations and deployment runbooks.

## Core documents

- `empirical-engineering.md` — evidence hierarchy, testing strategy, schema-evolution protocol, review policy, SLO discipline, and evidence ledger.
- `../deployment/production.md` — production topology and immutable artifact contract.
- `../deployment/backup-restore.md` — backup/restore procedure and recovery caveats.
- `../migrations/` — change-specific migration notes and durable decisions.
- `../../AGENTS.md` — executable operating contract for coding agents.
- `feature-registry.md` — registro de contratos protegidos, estado e evidências.
- `validation-matrix.md` — ligação entre contratos e validações executáveis.
- `regression-policy.md` — política de impacto, persistência, evidência e lacunas.
- `architecture-decisions/ADR-0001-windows-update-channel.md` — decisão do canal de atualização Windows e seus gates.

## Documentation rule

Keep three levels distinct:

1. **Contract** (`AGENTS.md`, harness, tests, DB constraints): what must remain true.
2. **Rationale/evidence** (`docs/engineering/`): why the contract exists and how strong the evidence is.
3. **Procedure** (`docs/deployment/`, `docs/migrations/`): how to deploy, recover, or execute a particular change.

Do not duplicate long procedures into `AGENTS.md`. Do not put an unmeasured reliability target into the harness. When a durable invariant changes, update all affected levels in the same PR.
