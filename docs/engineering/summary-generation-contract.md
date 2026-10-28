# Summary generation contract

This document records the runtime generation contract for `iaSummary`.

## Model selection and paid eligibility

The code preserves the historical volume-routing defaults: `claude-sonnet-5` at up to 100 persisted movements and `claude-opus-5` above 100, using total `step_count` rather than retrieval candidates. These defaults are not evidence of current account availability and do not authorize paid calls.

The current private authorization profile in `app.generation_authorization` permits only the exact configured `claude-sonnet-4-6` ID. Set `ANTHROPIC_MODEL` explicitly to that value for an authorized paid run; its override takes precedence over the volume defaults for every process size. The permit rejects other model IDs before network I/O. This repository policy is not account eligibility or live-call approval. Secret processes remain on the local deterministic path and never select an external generation model.

The routing defaults above are deliberately centralized in `app.rag` for compatibility. They do not establish active provider availability; paid eligibility is controlled exclusively by the exact model accepted by `app.generation_authorization`.

## Request contract

External generation requests use:

- `max_tokens=4000`;
- prompt caching on the stable system prompt through `cache_control={"type": "ephemeral"}`;
- no streaming, because the full result must pass post-generation validation before publication;
- zero or one correction request after a validation failure, bounded by the private permit's `max_regenerations`; one-shot mode and permits with no correction allowance make one message maximum. A failed validation is persisted with `passed=false` and is not published.
- Anthropic Structured Outputs via `output_config.format` with a closed JSON Schema;
- deterministic application-side rendering of the document title, allowlisted process header, party and representative lists, and Markdown section headings;
- public prompt version `process-summary-v7` requires labeled `Contexto do processo:` and `Andamento processual:` segments in synthesis; the existing summary schema is unchanged, previously persisted summaries remain readable, and secret summaries retain their independent local contract;
- bounded structured fields and arrays; the model cannot add arbitrary top-level keys or document sections.

The source document requested `temperature=0.2`. Rpy retains `REQUESTED_TEMPERATURE = 0.2` as documentary intent, while the current request capability flag omits custom sampling parameters. The preserved Sonnet 5/Opus 5 defaults are not the currently paid-authorized model profile; do not infer live compatibility from these request-shape tests.

## Telemetry

`process_summaries` persists generation metadata alongside the summary:

- `model`;
- `prompt_version`;
- `generation_ms`;
- provider-reported token usage in `usage`;
- `cache_hit`, derived from positive `cache_read_input_tokens`;
- optional `cost_usd` only when the provider response supplies a monetary cost field.

Rpy does **not** calculate cost from a hard-coded pricing table in the generation path. Pricing changes independently from application releases; a guessed or stale value would be misleading. Observability can add a separately versioned pricing layer later if required.

When the permit authorizes a correction, token/cache usage is accumulated across both provider messages. The private aggregation object is excluded from provider serialization and cannot leak into the correction prompt; without that allowance, the first failed result is persisted and no correction message is attempted.

## Public exposure

The `/v1` summary responses expose persisted generation telemetry inside `usage`. Invalid summaries remain unpublished: telemetry may exist for operational evidence, but `iaSummary` is returned only when persisted validation passed.

## Privacy

The existing provider boundary remains authoritative. Secret processes use local deterministic generation, and internal generation telemetry keys are explicitly excluded from provider serialization. No prompt, movement text, parties, credentials, or raw provider response is persisted as telemetry.


## Structured-output security boundary

The provider no longer authors the final Markdown document structure. Claude returns only schema-constrained narrative fields: `synthesis`, `timeline`, `current_status`, `attention`, `decisions`, `deadlines`, `related_processes`, and `attachments`.

`app.summary_output` owns the final renderer. Process identity and parties come from the normalized application context, not from generated values. Model strings are normalized to single logical lines before insertion, and leading Markdown/markup control characters are neutralized so a generated field cannot create a new heading or JSX control line.

This follows Anthropic's Structured Outputs contract, which uses constrained decoding to guarantee JSON-schema conformance, while retaining Rpy's deterministic semantic validator because schema conformance does not prove factual grounding.
