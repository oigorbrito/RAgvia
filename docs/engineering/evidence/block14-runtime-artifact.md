# Block 14 — runtime artifact reduction and CI evidence

## Scope and safety boundary

Base: `aa428f3e7aa4b3be2b6b913f33e407d76b9ca94a` (`work/ragvia-block10-consolidated`), the published PR #39 head. Work was performed in the separate `work/ragvia-block14-architecture-trim` worktree. This is local evidence for that base plus the working-tree changes described here; it is not a hosted CI result or a production release.

The concrete CI regression was that `scripts/smoke_offline.py` imports `httpx`, while the default runtime image installs `.[ocr]` and `httpx` was declared only in `.[dev]`. The fix gives the smoke a dedicated `.[smoke]` dependency and an `offline-smoke` Docker target. The default `runtime` target does not install that extra and contains only `scripts/migrate.py` and `scripts/provision_db_roles.py` under `/app/scripts`.

The two modules `app/local_demo.py` and `app/adversarial_mutations.py` are still present in the repository for development/staging and test use, but are excluded from the production build context. The developer-only `demo-seed` profile mounts its checkout source and scripts; it is absent from production Compose. No runtime service, endpoint, SQL migration, persisted state, authorization, tenant boundary, secrecy check, provider budget, queue rule, or core pipeline stage was removed or weakened. The production topology remains eight services: PostgreSQL, migration, API, egress proxy, attachment parser, two workers and one scheduler.

## Measured runtime artifact reduction

Method: count Git-tracked files under `app/` and `scripts/` that the Dockerfile copied into the runtime image at the base (`COPY app ./app`, `COPY scripts ./scripts`), respecting the two host-preview exclusions already in `.dockerignore`. Compare with the new runtime selection (`app/` less the two excluded modules, plus the two operational scripts). This measures packaged first-party content, not total repository size, dependency size, service count, or architectural complexity of the live request path.

| Measure | Base `aa428f3` | New runtime target | Change |
|---|---:|---:|---:|
| tracked `app/` + `scripts/` files | 147 | 86 | −61 (41.5%) |
| Python files | 126 | 80 | −46 (36.5%) |
| Python source bytes | 1,122,990 | 735,675 | −387,315 (34.5%) |
| nonblank, non-comment Python lines | 26,609 | 17,776 | −8,833 (33.2%) |
| scripts shipped in runtime image | 61 | 2 | −59 (96.7%) |
| app Python modules | 80 | 78 | −2 (2.5%) |

The line measure is physical lines excluding blank and comment-only lines; docstring lines are included. SQL/data and all source-tree tests remain untouched. Runtime services, dependency count (10 direct production dependencies), and the 37 existing migrations are unchanged. These measurements do not claim the remaining service topology or security boundaries are redundant.

## Vulnerability findings

The hosted PR #39 scans at the original published head reported 52 Python-audit rows: `certifi 2026.7.22` (`PYSEC-2026-4320`, duplicated across two advisory entries), `Pillow 12.3.0` (39 distinct advisories) and `pypdf 6.19.0` (11 CVEs). The original hosted logs are preserved locally at `D:/temp/ragvia-pr39-python-audit-full.log` and `D:/temp/ragvia-pr39-trivy-full.log`. The corresponding fix versions in that report were `certifi 2026.10.18/2026.10.19`, `Pillow 13.0.0`, and pypdf versions through `6.21.2`. The 31 hosted Trivy HIGH/CRITICAL library findings were 28 for Pillow and 3 for pypdf; the hosted report had no Debian findings at that scan time. The project dependencies now require/pin pypdf `6.21.2`, Pillow `13.0.0`, and certifi `2026.10.19`.

A local `pip-audit==2.10.1` run over the constrained `.[dev]` environment reported **no known vulnerabilities** in the resolved third-party packages. The private `rpy` distribution was skipped because it is not published on PyPI; this is not an audit of Rpy's source.

The built production image was scanned locally with Trivy `0.74.0`, vulnerability DB downloaded on 2026-10-30. Image identity: `rpy-block14-runtime:local`, image ID `sha256:11fe0fa3f97c689e32887b98bd8649f637d29b2f003beddf51ed0a2782df01ba`, Python `3.12.14`, Debian `13.7`.

- Full HIGH/CRITICAL report (`--ignore-unfixed=false`): **71 Debian package-CVE hits**, 70 HIGH and 1 CRITICAL, across 28 unique CVE IDs; no Python package findings. All 71 had no `FixedVersion` in this Trivy database. Statuses were 69 `affected`, one `will_not_fix`, and one `fix_deferred`.
- The repository's existing fixable-vulnerability gate (`--ignore-unfixed=true`) found **zero fixable HIGH/CRITICAL findings**. No scanner was disabled and no new suppression was added. This is not a claim that the image is vulnerability-free. The remaining critical finding is Debian `libxml2` CVE-2026-6653; the local report listed no fixed version. Other unique IDs were: `CVE-2025-69720`, `CVE-2026-12064`, `CVE-2026-16742`, `CVE-2026-36849`, `CVE-2026-52490`, `CVE-2026-54369`, `CVE-2026-73066`, `CVE-2026-74860`, `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`, `CVE-2026-78410`, `CVE-2026-8458`, `CVE-2026-86138`, `CVE-2026-86139`, `CVE-2026-86140`, `CVE-2026-86142`, `CVE-2026-86143`, `CVE-2026-86144`, `CVE-2026-88047`, `CVE-2026-88048`, `CVE-2026-88051`, `CVE-2026-88052`, `CVE-2026-88053`, `CVE-2026-88806`, `CVE-2026-88807`, and `CVE-2026-9538`.

These are current scan observations, not an exploitability assessment. The code changes do not claim to fix the unfixed Debian findings. The full local Trivy JSON is preserved at `D:/temp/ragvia-block14-trivy-0.74.0.json` (SHA-256 `cbef5129fb486c9d6410cf34ff77c491eb1775e7d3146236467f49ac339fa697`). Repeat the scan on the exact hosted image/head before release.

## Local verification

Executed against the working tree before this evidence document was added; the document does not enter the runtime image:

- `python scripts/project_harness.py`: pass (26 guardrail/wiring observations, including migration and release harnesses).
- `git diff --check`, `ruff check app scripts tests`, and `node tests/frontend_behavior_test.mjs`: pass.
- Linux/Python 3.12.14 unit suite: `1477 passed, 3 skipped`; `python -m pip check`: pass. A native Windows attempt was not suitable evidence for Unix sockets and POSIX-shell tests (12 platform-specific failures); the canonical Linux run passed.
- PostgreSQL 16 integration suite against a disposable, isolated pgvector container/network: `180 passed`, zero failures/errors/skips; `scripts/validate_candidate_postgres.py` accepted the JUnit result. The test environment had empty external-provider credentials.
- `docker build --target runtime` and `docker build --target offline-smoke`: pass. `sh scripts/verify_image_runtime.sh rpy-block14-runtime:local`: pass; runtime scripts were exactly `migrate.py` and `provision_db_roles.py`; `app.local_demo` and `app.adversarial_mutations` were absent.
- `bash scripts/smoke_offline.sh`: pass against disposable PostgreSQL 16; all 37 migrations applied, a valid summary was published, cross-tenant access returned 404, and the smoke reported `providers=0`.
- Synthetic RAG, offline pipeline and offline generation baseline commands all passed. These are fixture/fake-provider contract evidence, not live-provider quality, clinical/legal efficacy, production latency, or cost evidence.
- The developer `demo-seed` path passed with an explicit valid synthetic bearer mapping; no provider credentials were set.

No paid provider was called. The exact hosted checks for the new commit have not run at the time this document was written. PR #39 must remain Draft and stacked on PR #38 until hosted checks on the published SHA are inspected; neither PR is merged and `main` was not changed.
