# Local summaries for secret proceedings

Secret proceedings no longer call the external generation provider.

The application already blocks parties, subjects, movements and embeddings when `secrecy_level > 0`. Calling Anthropic after that boundary added cost and an avoidable external dependency without providing material information. The secret path is now deterministic and local.

## Output contract

The local summary contains:

- an explicit notice that procedural details are restricted by secrecy;
- the process class when present;
- a strict subset of already-allowed header metadata: instance, area, justice description, county, state and city.

It deliberately omits CNJ, parties, subjects, movements, the generic header `name` field and case amount from the rendered secret summary.

## Provider boundary

`ANTHROPIC_API_KEY` is not read and an Anthropic client is not created for a secret process. The existing `_provider_payload` secret boundary remains in place as defense-in-depth for direct contract tests and future refactors.

Secret summaries are persisted with:

- `model = local-deterministic`
- `prompt_version = secret-summary-v1`

The normal post-generation validator still runs before persistence. Non-secret processes continue to use Claude Sonnet 5 and the existing second-attempt validation flow.
