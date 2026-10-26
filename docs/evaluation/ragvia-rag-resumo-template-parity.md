# Judit `summary_ia` parity — offline evidence and limits

## Objective and boundary

This change closes the **output-shape** gap without replacing RAGVIA's validated structured-summary pipeline or its public `iaSummary` contract. `juditSummary` is an additive, tenant-authorized projection of the same persisted summary, with a Judit `response_type: "summary"` item and deterministic `summary_ia` presentation. It is not a new webhook, a Judit write, or a route that accepts the external Judit summary as evidence.

The reference capture was processed locally in both checkouts. No source/output summary body was written to the repository, no PostgreSQL row was created, and no Judit, Claude, embedding, or other external call was made. The donor checkout's pre-existing working-tree changes were preserved and were part of the measured donor behavior; they were not copied into RAGVIA.

## Reproduction

Run from the RAGVIA checkout with the private capture and donor checkout supplied explicitly:

```bash
PYTHONPATH=. python scripts/evaluate_judit_template_parity.py \
  --capture <private>/judit_exemplo_ORIGINAL.json \
  --donor-root <private>/rag_resumo \
  --report <private-output>/template-parity.json
```

The evaluator prints only metadata/counts/hashes. The report path must remain outside the repository because it fingerprints outputs derived from a real judicial-process capture.

Observed run:

- Capture SHA-256: `b5decf20e6bb330a7a58974711b7ec8e72215d74568e014e8cf1ced14ee916aa`.
- Capture: one lawsuit, one accompanying external `summary`, 13 movements, 2 attachments, secrecy level 0.
- RAGVIA base commit at execution: `6d8da8ea9d6e01208ca21701c23f26194621ea84`; target changes were uncommitted. The private report records each dirty file hash and the tracked diff SHA-256 `4b6fcec6dd06558eee2c7894dd6f051501d92662f7c4ccd27cd3f997be23941b`.
- Donor commit: `0d0f8cbb495292a3f3d61fba75428f3136059ea8`; its existing modifications to `resumo/pipeline.py`, `scripts/gerar.py`, `tests/test_gate_claude.py`, and `tests/test_resumo.py` remained untouched. Their file hashes are recorded in the private machine-readable report.
- Run ID: `115bcc29-749a-4a38-9b8c-f615ef4e8fd5`; private result: `D:/temp/ragvia-parity-evidence-20261026/template-parity.json`.

## Differential observations

| Surface | Donor | RAGVIA | Finding |
|---|---:|---:|---|
| Lawsuit/movement parsing | 1 / 13 | 1 / 13 | All 13 source-number-aligned movement kinds, act dates and lexical details matched in this capture. This is one capture, not a general legal-quality result. |
| Process actors | 4 flattened entries | 3 parties + 2 representatives | Deliberate, retained distinction: RAGVIA does not promote a lawyer to a litigant. The compatibility renderer has separate `Partes` and `Representantes` sections. |
| Registry | 2 classifications, 3 subjects | 2 source classifications retained; 3 subjects | The canonical generated document still identifies the primary class; it does not elevate the donor's heuristic discussion of a suspected secondary class into a model fact. |
| Knowledge | 33 donor entries; 19 retrieved chunks (10 class/subject) | 0 official TPU definitions; 14 supplemental legal excerpts (1 class, 3 subject, 6 movement, 4 concept) | Coverage/mapping is not equivalent. RAGVIA's unmatched TPU codes are not silently mapped from the donor KB. Any expansion needs independently sourced and reviewed mappings/definitions. |
| Prompt | 11,993 user-prompt characters + 2,856 system-prompt characters | 18,098 user-prompt characters + 20,104 system-prompt characters | Prompt hashes and counts are in the private report. Neither prompt was sent. The donor prompt contains the source-derived text “Ignore a área”; RAGVIA excludes lexical annotations from the provider payload. This security divergence is intentional. |
| Template rendering | 7,734 characters; 5 headings; 3 unique rendered dates | 5,904-character compatibility view; 6 headings; 3 unique rendered dates | Two rendered dates match. RAGVIA preserves separate representatives and uses escaped, deterministic markup. It does not reproduce unsupported party-role/legal explanations from the donor template; one date-format/content difference remains visible rather than overstated as full parity. |
| Provenance/validation | No post-render factual/claim validation in the donor template path | 17 material claims, 45 evidence references; existing structured/factual/claim validators passed | The RAGVIA offline template candidate used the existing schema, validator, claim-evidence verifier and renderer. Its UUID references were local evaluation identifiers only, not persisted IDs. |
| External summary separation | Pipeline selects lawsuit records | Provider context contains normalized lawsuit data only | The captured external `summary` item was not included in either generation context. |
| Sensitive identifiers | No full source personal identifiers found in rendered output | No full source personal identifiers or representative registration found in either RAGVIA rendering | This is a content scan for this capture, not a general privacy proof. |

The local deterministic RAGVIA template candidate is an **evaluation adapter**, not a production fallback or a provider implementation. It builds a candidate against the canonical context and exercises the production validation/rendering components without persistence. Donor and RAGVIA template text is not identical: the donor template contains role, liability and procedural-effect explanations that RAGVIA's closed, evidence-bound contract does not permit it to assert from this capture.

## Output and API behavior

`GET /processes/{cnj}`, `GET /v1/resumos/{job_id}`, and `GET /v1/processos/{cnj}/resumo` retain their existing `iaSummary` value/representation and may additionally return `juditSummary`:

```json
{
  "response_type": "summary",
  "response_data": {
    "data": ["<escaped, deterministic summary_ia string>"],
    "origin_id": null,
    "origin": "response"
  },
  "tags": {"gerador": "<persisted model>", "alertas": [], "fontes": ["<public source identifiers>"]},
  "created_at": "<persisted summary timestamp>"
}
```

The actual `fontes` list contains only identifiers already exposed in the authorized `sources` response. The wrapper is emitted only after the same persisted validation/publication check as `iaSummary`; another tenant still receives no process data. Secret-process rendering uses only the existing restricted context. The RAGVIA frontend continues to render/copy `iaSummary`, not the compatibility envelope.

## Remaining gates

- **Real Claude generation and provider acceptance: PENDING.** This run did not call Claude and cannot establish live prompt compatibility, real-model quality, latency, cost, or legal-process acceptance. The existing approval, timeout/retry, validation, one-regeneration, provenance and publication gates remain the required route for a later explicitly authorized run.
- **Knowledge parity: NOT COMPLETE.** The observed mismatch in class/subject knowledge remains unresolved pending provenance/licensing review and verified source mappings; donor entries are not treated as authoritative evidence.
- **Secret case: NOT exercised by this real capture.** Existing secret-summary tests and the deterministic local path remain the evidence for that boundary.
- **Frontend: existing browser renderer remains based on `iaSummary`.** The behavior test now proves the additive `juditSummary` wrapper does not replace or leak into the rendered/copy text. A live external consumer of the Judit compatibility item was not tested.

Do not describe this work as complete parser/knowledge/prompt/Claude parity. It demonstrates a real-capture, provider-free template comparison and an additive compatible output contract, with the stated differences preserved.
