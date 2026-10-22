# Security policy

Rpy processes judicial data and integrates with external providers, so security reports should minimize unnecessary exposure of credentials, process content and tenant information.

## Reporting a vulnerability

Do not open a public issue containing an exploitable vulnerability, secret, access token, webhook token, provider credential, raw judicial-process payload or tenant data.

Prefer GitHub's private vulnerability-reporting / Security Advisory flow for this repository when it is available. If private reporting is not available, contact the repository maintainer privately through the contact methods exposed on the maintainer's GitHub profile before sharing technical details publicly.

A useful report should include:

- affected commit/version;
- affected component or endpoint;
- minimal reproduction steps;
- expected versus observed behavior;
- impact and required preconditions;
- whether credentials or real process data were involved;
- any temporary mitigation already tested.

Use synthetic or redacted data whenever possible.

## Scope priorities

Reports are particularly important when they involve:

- tenant-isolation or authorization bypass;
- secret judicial cases reaching an external provider;
- credential or webhook-secret disclosure;
- cross-tenant cache/data leakage;
- unauthenticated operational endpoints;
- SQL injection or unsafe dynamic SQL;
- duplicate irreversible provider side effects caused by retry/idempotency failures;
- mutation of finalized historical process data;
- retention/expunge failures that leave sensitive source data behind.

## Supported version

Until a newer release is published, security fixes target the current `main` branch and the latest published `0.1.x` release line.

Provider acceptance against real Judit/Anthropic/OpenAI/Cohere accounts or real BGE artifacts is operational validation and must not be used as a substitute for a minimal synthetic security reproduction. Follow `docs/release/provider-acceptance.md` and never publish acceptance credentials or raw judicial payloads.
