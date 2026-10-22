# Generation telemetry

Provider-reported generation telemetry is persisted with each process summary.

- token usage is recorded from the provider response and accumulated across the single permitted correction attempt;
- cache hits are derived only from positive provider-reported cache-read tokens;
- monetary cost is stored only when the provider reports it; Rpy does not infer or hard-code pricing;
- internal aggregation keys are excluded from provider serialization;
- `/v1` exposes the persisted metadata inside `usage` without exposing prompts, source payloads, credentials, or raw provider responses.

Secret processes remain on the local deterministic path and do not call the external generator.
