# Python dependency lock

Production and CI installation are constrained by `requirements/constraints.txt`. The file records the exact runtime and test dependency versions validated together on Python 3.12.14.

`pyproject.toml` remains the package compatibility declaration. The constraints file is the reproducible deployment/test resolution. Direct and development dependencies declared in `pyproject.toml` must have an exact `==` entry in the lock; unit tests enforce this contract.

The production Dockerfile also pins `pip==26.2.1`, while the isolated build backend is pinned to `setuptools==80.9.0`. CI and the release test job use Python 3.12.14 to match the currently pinned production base image line.

Dependency updates should be deliberate: update the relevant compatibility range if needed, regenerate/review the complete constraints set, run the full PostgreSQL CI and container smoke test, then promote the resulting immutable application image digest. Do not run an unconstrained dependency upgrade during deployment.

This lock fixes package versions, not artifact hashes. A future hardening step can add hash-verified wheel/source artifacts if the project needs stronger package-index tamper resistance or byte-for-byte dependency artifact verification.
