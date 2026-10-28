# Local iaSummary development runner

`python scripts/run_local_iasummary.py --input <captured-response.json> --output-dir <private-temp-dir>` runs the existing `generate_summary()` path in an explicitly local extractive mode.

The runner:

- requires exactly one normalized `response_type: lawsuit`; it does not pass a `summary` response to generation;
- uses the existing Judit normalizer, short-process retrieval/ranking, local legal-knowledge lookup, claim validation, and structured Markdown renderer;
- refuses secret proceedings, never creates a database pool, requires no tenant/process link, never persists/publishes, and forbids provider construction in the runner process;
- reads an optional `response_type: summary` only after generation, for deterministic comparison;
- writes Markdown and a JSON validation/provenance report to the requested local output directory. These artifacts may contain judicial-process data and must be handled accordingly.

This mode is a development extraction, not Claude output, not a substitute for the authorized production generation path, and not evidence of API publication. The short-process retrieval path selects all movements; vector retrieval is intentionally not invoked because the runner has no database. The comparison validator is a consistency gate, not a comprehensive semantic or legal review.
