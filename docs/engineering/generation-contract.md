# Generation contract

The process-summary generator chooses the Anthropic model from the normalized process context, not from the number of retrieved candidates.

- `claude-sonnet-5` is the default for processes with at most 100 movements.
- `claude-opus-5` is selected only when `step_count > 100`.
- The output ceiling is 4,000 tokens for both routes.
- The system prompt remains cacheable with `cache_control: {"type": "ephemeral"}`.
- Streaming is not enabled because the complete output must pass post-generation validation before publication.
- At most one corrective regeneration is performed after a validation failure.
- Secret processes remain on the local deterministic path and do not instantiate the external generator.

## Sampling

The product target records `temperature = 0.2`, but the configured current-generation Anthropic models do not accept custom sampling parameters. The request therefore intentionally omits `temperature`, `top_p`, and `top_k`. This is a provider-compatibility constraint, not an implicit change of the product target.

## Movement count

Model routing uses `step_count`, which is captured before retrieval/reranking. A process with 101 total movements therefore routes to Opus even if only a smaller candidate set is ultimately sent in the prompt.

## Telemetry

Persisted provider usage/cache/cost telemetry is tracked separately in #126. This document records only the model-selection and request-parameter contract implemented in the first #126 block.
