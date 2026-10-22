## Objective

<!-- What failure mode, user need, or measurable objective does this PR address? -->

## Invariants / risks

<!-- Which data-integrity, privacy, recovery, provider, tenancy or deployment invariants are relevant? -->

## Change

<!-- Describe the smallest coherent implementation change. -->

## Evidence

<!-- Tests, CI, restore drill, smoke, benchmark or reproduction. Do not mark green before the exact head is green. -->

- [ ] `git diff --check`
- [ ] migration harness when applicable
- [ ] unit tests
- [ ] PostgreSQL integration tests when applicable
- [ ] frontend behavior tests when applicable
- [ ] offline smoke when release/deployment behavior changes

## Operational / migration impact

<!-- Rollout, rollback, schema compatibility, retention, provider/data-boundary or runbook changes. Write "none" when not applicable. -->

## Security and providers

- [ ] no secrets or real judicial-process payloads were added to code, fixtures, logs or PR text
- [ ] automated validation used no paid-provider calls unless explicitly authorized and documented
