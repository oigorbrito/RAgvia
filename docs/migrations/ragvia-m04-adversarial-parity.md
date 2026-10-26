# M04 adversarial qualification of migrated summarization behavior

## Scope and decision

Qualified the synthetic-data behavior at base commit `64271384a37b960c0b04fd7328bddbf29f66cc50` (DOC01/PR #16), without calling a live provider or using case data. No runtime changes are made. The temporal, factual, and retrieval-focused synthetic checks passed, but the adversarial mutation below exposes a real limit in deterministic semantic verification. It is recorded as a release/review limitation rather than patched with a broad or speculative rule: the current verifier has no general way to distinguish an unsupported legal explanation from valid paraphrased synthesis without introducing another semantic authority or rejecting legitimate narrative claims.

## Tested surface and evidence

- Temporal extraction/validation: `tests/test_rag_temporal_facts.py` and `tests/test_validation_contract_rules.py`.
- Source-backed claim checks: `tests/test_claim_verification.py`.
- Legal retrieval/parity and writing oracle: `tests/test_legal_knowledge.py` and `tests/test_writing_parity_evaluation.py`.
- Synthetic process fixture: `tests/fixtures/judit/parser_parity_synthetic.json`; no real process data or provider request.
- Combined targeted result: `pytest -q tests/test_rag_temporal_facts.py tests/test_validation_contract_rules.py tests/test_claim_verification.py tests/test_legal_knowledge.py tests/test_writing_parity_evaluation.py` — **79 passed**.
- Temporal tests exercise explicit dates/periods and negated/cancelled hearing language; claim checks exercise wrong dates/amounts/party/process identifiers, exact source support, and unanchored material claims. This is a deterministic fixture qualification, not a prevalence or live-model quality estimate.
- The existing DOC01 PostgreSQL → canonical API `iaSummary` → browser smoke evidence is recorded in `docs/evaluation/evidence/doc01-browser-smoke.json` and its companion image. It was not rerun in this qualification; it is evidence for that earlier DOC01 head only.

## Reproduced adversarial limitation

A synthetic `synthesis` claim combined source-backed anchors (a fixture party and hearing date) with an additional unsupported assertion: “A lei assegura automaticamente a procedência do pedido, ainda que não haja prova.” The claim cited the process and movement sources. `verify_material_claims()` returned `supported` with reason `deterministic_facts_present_across_cited_sources`; `verification_errors()` returned no errors. The deterministic verifier checks anchors such as dates, amounts, parties, and exact text, but does not evaluate the truth of free-form legal propositions embedded alongside those anchors. The mutation therefore demonstrates a false sense of whole-claim support, not that the legal assertion itself was evaluated.

This mutation was an isolated Python invocation using `_context()` from `tests/test_claim_verification.py`; it was not added as a passing regression test because it currently demonstrates the gap. The current API tests also explicitly permit some non-material narrative claims to remain `not_evaluated`. A safe general fix needs an explicit product decision and a tested semantic-verification strategy; exact-quote-only validation or a guessed legal-keyword denylist would risk rejecting valid summaries and is not justified by this qualification alone.

## Boundaries and blockers

- `TEST_DATABASE_URL` was not configured, so PostgreSQL adversarial/integration tests were blocked and not represented as passing.
- No browser smoke was rerun because no presentation code changed.
- No live/paid provider calls, real case data, or model-quality/latency/cost claims.
- Synthetic evidence cannot establish what an actual model will generate.

## Robustness decision

**Do not claim adversarial robustness for unsupported legal reasoning.** Existing deterministic checks support temporal and anchored factual regression coverage, but they do not prove free-form legal assertions. Keep PR #16 draft and do not merge/release on the strength of these synthetic results. Before enabling/promoting generation, decide whether legal explanations must be exact retrieved-source quotations, require a separate verified support mechanism, or may remain explicitly unverified. Re-run this mutation as a rejection test once that product/verification contract is selected.
