# Local iaSummary development runner

`python scripts/run_local_iasummary.py --input <captured-response.json> --output-dir <private-temp-dir>` runs the existing `generate_summary()` path in an explicitly local extractive mode.

The runner:

- requires exactly one normalized `response_type: lawsuit`; it does not pass a `summary` response to generation;
- uses the existing Judit normalizer, short-process retrieval/ranking, local legal-knowledge lookup, claim validation, and structured Markdown renderer;
- converts parser heuristics to static allowlisted warnings; raw annotation messages are not forwarded to generation;
- refuses secret proceedings, never creates a database pool, requires no tenant/process link, never persists to the official application store or publishes, and forbids provider construction in the runner process;
- uses only the `response_type: lawsuit` data; a `response_type: summary` is not read, compared, passed to generation, or presented as RAGVIA output;
- writes Markdown and a JSON validation/provenance report to the requested local output directory. These artifacts may contain judicial-process data and must be handled accordingly.

This mode is a development extraction, not Claude output, not a substitute for the authorized production generation path, and not evidence of API publication. The short-process retrieval path selects all movements; vector retrieval is intentionally not invoked because the runner has no database. The deterministic validator is a consistency/evidence-link gate, not a comprehensive semantic or legal review.

## Private local presentation

After an existing result has been generated, `python scripts/serve_local_iasummary_preview.py --capture <capture.json> --output-dir <private-temp-dir> --open-browser` revalidates the capture hash, every reported movement/claim reference and the no-provider/no-official-persistence flags before displaying the existing Markdown and normalized movements in the canonical `app/frontend` renderer. It does not rerun generation, read the `summary` reference for presentation, create a tenant/process association, or call `app/api.py` process routes. The frontend changes presentation labels only in this opt-in local session; the official API publication contract is unchanged.

The preview server binds only to `127.0.0.1`, requires a random in-memory Bearer token delivered in the URL fragment, removes that fragment before requesting data, checks the Host/Origin and compares tokens timing-safely. The payload route is a temporary Python static-preview handler, not a production FastAPI endpoint. Stop the process to revoke the session. The view now includes the normalized source movements, with each displayed movement hash-checked against the runner's evidence map; tenant/acquisition controls remain hidden. `--browser-smoke` uses Chromium to verify the rendered summary, movement text/references and clipboard projection.

### Import and persist in isolated development storage

For an operator-selected JSON file, start the same canonical frontend in local-import mode:

```powershell
python scripts/serve_local_iasummary_preview.py --import-mode --store-dir "D:\private\ragvia-local-imports" --open-browser
```

Select the existing JSON in the page and explicitly choose **Importar e gerar resumo extrativo**. The server accepts at most 10 MiB and requires exactly one non-secret `response_type: lawsuit` in `page_data`. The existing preview validator is currently capture-specific (13 movement links and 21 claims); inputs that do not pass that gate fail closed and are not stored. The server runs `scripts/run_local_iasummary.py` in a child process with provider credentials/database settings removed and external-provider feature flags disabled. A `response_type: summary` is neither read for content nor compared or passed to generation. No Judit/DataJud acquisition, API process route, database, tenant/process association, Claude call, official summary row or publication is created.

A successful import stores the exact source capture, Markdown, validation/provenance report and a manifest under `<store-dir>/<capture-sha256>/`. This is content-addressed, local development file storage—not official application persistence. Reimporting identical bytes revalidates and reuses the stored result without regenerating; invalid existing records are not overwritten. The exact source file is retained because the preview rechecks its hash and renders movements after restart. It can contain the full original capture, including a supplied `summary` reference, so the store and screenshots are sensitive judicial-process data. Choose a private directory outside the checkout, restrict its NTFS ACL/POSIX permissions to the authorized operator and administrators, and apply the organization's retention/deletion policy. File deletion does not promise secure physical erasure.

The import POST is same-origin, Bearer-authenticated, size-bounded, and accepted only by this host-side loopback server; the content-addressed store is never served as static files. The page labels the result as local extractive output, displays its movements and claim evidence, and distinguishes local file persistence from the absence of official `iaSummary`/`juditSummary` publication. The deterministic validation is structural/evidence-link validation, not proof of legal correctness or semantic entailment. The development flow does not replace the authorized Judit acquisition and Claude generation paths.

For a real-browser import check (this performs an import only if the capture hash is not already present; otherwise it verifies reuse), use private paths:

```powershell
python scripts/serve_local_iasummary_preview.py --browser-import-smoke --capture "<existing-capture.json>" --store-dir "D:\private\ragvia-local-imports" --screenshot-out "D:\private\iasummary-import.png" --evidence-out "D:\private\iasummary-import-evidence.json"
```

`--browser-import-smoke` uploads through the same UI and endpoint, opens Chromium, checks rendered Markdown/clipboard and all movement/claim references, and records browser requests and hashes. This verifies the implemented local flow for the specified capture; it does not establish semantic/legal correctness, Claude compatibility or official publication. The screenshot/evidence are private process data and must never be committed or attached to a PR.

These utilities are host-only and are excluded by `.dockerignore`; `scripts/verify_image_runtime.sh` rejects their presence in a deployable image. Shipping a raw-capture runner and preview inside the runtime image would expose a second, non-published process-summary path and blur the boundary with official `iaSummary`/`juditSummary`. This is the product decision for the current local-only workflow; revisit it only after an explicit approval and privacy/security review of containerized homologation.
