# Immutable CI dependency references

GitHub Actions and CI service images are pinned to immutable commit/image digests.

The human-readable major version remains in an inline comment next to each action SHA. Updating an action therefore requires an explicit dependency change: resolve the intended tagged release to its commit SHA, review the upstream change, replace the SHA and let the full CI suite validate the result.

The PostgreSQL/pgvector service used by CI and the release-image test job uses the same pg16 index digest as the production contract. This prevents a moving upstream tag from changing test behavior independently of production.

Do not replace pinned workflow references with floating major tags such as `@v4` or service tags such as `:pg16` merely for convenience. Dependency-update automation may propose SHA changes, but the repository should continue to store immutable references.
