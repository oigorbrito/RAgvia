# Release versioning

Rpy uses Semantic Versioning 2.0.0 for release identities.

The project is in the `0.y.z` initial-development phase. Release tags are immutable: once a
version has been published, later repository changes require a new version and must never be
presented as the contents of an existing tag.

## Published releases

Published immutable baselines:

- `v0.1.0` — historical first release, tag commit
  `ea3399ec4780756bf149f2ebd2bbb3bd3a790705`;
- `v0.1.1` — published 2026-09-25, release target
  `c54f8bfd56a7661511eee56db25de54183fff376`.

Do not move, recreate or retarget either published tag.

## Current main

Current `main` contains substantial work after `v0.1.1`, including live Judit acceptance
evidence, production callback configuration, and the persistent-staging readiness gate.

The next release identifier has **not** been selected. Until the release owner explicitly selects
the next identifier, `pyproject.toml` may continue to show the last published package version
(`0.1.1`); that metadata does not mean current `main` is identical to `v0.1.1`.

Release preparation is tracked in `docs/release/next-release.md`. No new immutable tag may be
created until the exact release head has passed the complete gates and its published image digest
has been smoke-tested and attested.

## Version selection rule

When the release owner selects the next identifier:

1. review changes since the previous tag against the documented public API and deployment contract;
2. select a SemVer-compatible version appropriate for the compatibility scope;
3. update `pyproject.toml` and create matching release notes in one release-preparation change;
4. run the complete project/release gates on the exact release head;
5. build the image once in trusted CI, smoke-test its digest, and record/verify its attestation;
6. create the new immutable tag only after those gates are green.

A pre-release identifier such as `-rc.1` may be used when a formally versioned candidate is needed
before the final release. Its use must be explicit; the repository does not infer or generate one
automatically.

## Source

Policy basis: Semantic Versioning 2.0.0, https://semver.org/. A released version must not be
modified; later modifications require a new version. Major version zero remains the
initial-development phase.
