# Reranker contract

The retrieval pipeline may obtain up to 50 candidates and apply a separate reranker before selecting approximately 15 movements. `rerank_steps()` keeps that boundary independent from the scoring provider.

The scorer supplies only candidate scores. Hard filters must already have been applied, and the post-ranking step always preserves the first, last, five most recent and milestone movements. Ties are resolved by `step_number`, making the selection deterministic.

## Implemented providers

Reranking is disabled by default through `RERANKER_ENABLED=false`. When enabled, exactly one provider is selected by `RERANKER_PROVIDER`.

- `bge` is the default self-hosted provider and uses `BAAI/bge-reranker-v2-m3` through FlagEmbedding.
- `cohere` uses Cohere's Rerank v2 endpoint and defaults to `rerank-v4.0-pro`.
- Cohere requires separate explicit `ALLOW_EXTERNAL_RERANKER=true` authorization and `COHERE_API_KEY`.
- Cohere never acts as an automatic fallback for BGE, and BGE never silently replaces a selected Cohere deployment.
- Secret processes return before reranker resolution and therefore never cross the external Cohere reranking boundary.
- Provider failure is explicit; the selection layer does not silently publish a differently ranked context.

The Cohere adapter sends only the already-filtered candidate movement text and receives candidate indices plus relevance scores. The existing provider-neutral selection code maps those scores back to the candidate movement IDs and applies the same mandatory-movement preservation rules.

## Evaluation boundary

Synthetic CI evaluation proves deterministic orchestration and mandatory-movement preservation without calling an external provider. It is not evidence of real BGE or Cohere quality, latency or cost.

The production-default BGE path still requires the real benchmark documented in `docs/evaluation/reranker-benchmark.md` before issue #121 can close. Cohere remains optional and deployment-authorized; enabling it does not remove the BGE benchmark requirement.
