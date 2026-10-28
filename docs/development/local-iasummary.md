# Local iaSummary development runner

`python scripts/run_local_iasummary.py --input <captured-response.json> --output-dir <private-temp-dir>` runs the existing `generate_summary()` path in an explicitly local extractive mode.

The runner:

- requires exactly one normalized `response_type: lawsuit`; it does not pass a `summary` response to generation;
- uses the existing Judit normalizer, short-process retrieval/ranking, local legal-knowledge lookup, claim validation, and structured Markdown renderer;
- converts parser heuristics to static allowlisted warnings; raw annotation messages are not forwarded to generation;
- refuses secret proceedings, never creates a database pool, requires no tenant/process link, never persists/publishes, and forbids provider construction in the runner process;
- reads an optional `response_type: summary` only after generation, for deterministic comparison;
- writes Markdown and a JSON validation/provenance report to the requested local output directory. These artifacts may contain judicial-process data and must be handled accordingly.

This mode is a development extraction, not Claude output, not a substitute for the authorized production generation path, and not evidence of API publication. The short-process retrieval path selects all movements; vector retrieval is intentionally not invoked because the runner has no database. The comparison validator is a consistency gate, not a comprehensive semantic or legal review.

## Private local presentation

After an existing result has been generated, `python scripts/serve_local_iasummary_preview.py --capture <capture.json> --output-dir <private-temp-dir> --open-browser` revalidates the capture hash, 13 movement links, 21 claim references and the no-provider/no-persistence flags before displaying the existing Markdown in the canonical `app/frontend` renderer. It does not rerun generation, read the `summary` reference for presentation, create a tenant/process association, or call `app/api.py` process routes. The frontend changes presentation labels only in this opt-in local session; the official API publication contract is unchanged.

The preview server binds only to `127.0.0.1`, requires a random in-memory Bearer token delivered in the URL fragment, removes that fragment before requesting data, checks the Host/Origin and compares tokens timing-safely. The payload route is a temporary Python static-preview handler, not a production FastAPI endpoint. Stop the process to revoke the session. `--browser-smoke` uses Chromium to verify the rendered summary and clipboard projection and stores a screenshot/evidence JSON outside the checkout. Capture, Markdown, validation JSON, screenshot and browser evidence all contain judicial-process data: pass explicit paths in both commands, use storage readable only by the homologation operator and administrators (for example owner-only POSIX permissions or restrictive NTFS ACLs), and never commit or attach these artifacts to a PR.

These two utilities are host-only and are excluded by `.dockerignore`; `scripts/verify_image_runtime.sh` rejects their presence in a deployable image. Shipping a raw-capture runner and preview inside the runtime image would expose a second, non-published process-summary path and blur the boundary with official `iaSummary`/`juditSummary`. This is the product decision for the current local-only workflow; revisit it only after an explicit approval and privacy/security review of containerized homologation.
