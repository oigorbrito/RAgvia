# Generation contract

The process-summary generator retains volume-routing compatibility defaults, selected from the normalized total `step_count`, not the number of retrieved candidates:

- `claude-sonnet-5` is the default for processes with at most 100 movements.
- `claude-opus-5` is selected only when `step_count > 100`.
- These legacy defaults are not evidence of current provider availability and are not eligible under the current paid-generation permit.
- The current `generation_authorization.PROPOSED_MODEL` is `claude-sonnet-4-6`. A paid request requires that exact explicit `ANTHROPIC_MODEL`; the override takes precedence over volume routing, so this approved profile uses Sonnet 4.6 at every movement count. Other IDs fail closed before the provider request.
- This is a repository authorization policy, not evidence that the account is enabled or that a live call is approved.

The output ceiling is 4,000 tokens. The system prompt is cacheable with `cache_control: {"type": "ephemeral"}`. Streaming is disabled because the complete output must pass post-generation validation before publication. Secret processes remain on the deterministic local path and do not instantiate the external generator.

A validation correction is optional, not implicit: the provider facade derives `validation_regeneration_allowed` from the private permit's message allowance. At most one correction is possible, and only when the permit authorizes it and one-shot mode is off. One-shot mode permits zero corrections. When correction is disallowed, the first validation failure is persisted with `passed=false` and remains unpublished.

## Sampling

The product target records `temperature = 0.2`, but the current request capability flag intentionally omits `temperature`, `top_p`, and `top_k`. This documents the application request shape; it is not live compatibility evidence for the paid-authorized model.

## Movement count

Without an explicit `ANTHROPIC_MODEL` override, model routing uses `step_count`, captured before retrieval/reranking. A 101-movement process therefore selects the legacy Opus default even if retrieval sends fewer candidates. An explicit paid-authorized override takes precedence, as described above.

## Telemetry

Persisted provider usage/cache/cost telemetry is tracked separately in #126. This document records only the model-selection and request-parameter contract implemented in the first #126 block.
