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
- RAGVIA base commit at execution: `15ad1c6ed478ede7044b7ace3df6b7778efa2748`; the date-format/evaluator changes were uncommitted. The private report records each dirty code/test file hash and the tracked diff SHA-256 `82d0d807a7a031a5777322a35bfeaddc8ac2232f666b6644de5c68622ba15c77` (the evaluation narrative is excluded from that self-referential fingerprint).
- Donor commit: `0d0f8cbb495292a3f3d61fba75428f3136059ea8`; its existing modifications to `resumo/pipeline.py`, `scripts/gerar.py`, `tests/test_gate_claude.py`, and `tests/test_resumo.py` remained untouched. Their file hashes are recorded in the private machine-readable report.
- Run ID: `509d28b1-b3ae-4596-8ceb-b3faeada26ac`; private result: `D:/temp/ragvia-parity-evidence-20261026/template-parity-closeout.json`. It includes the ID/source-reference crosswalk and definition hashes, not capture text or full generated summaries.

## Differential observations

| Surface | Donor | RAGVIA | Finding |
|---|---:|---:|---|
| Lawsuit/movement parsing | 1 / 13 | 1 / 13 | All 13 source-number-aligned movement kinds, act dates and lexical details matched in this capture. This is one capture, not a general legal-quality result. |
| Process actors | 4 flattened entries | 3 parties + 2 representatives | Deliberate, retained distinction: RAGVIA does not promote a lawyer to a litigant. The compatibility renderer has separate `Partes` and `Representantes` sections. |
| Registry | 2 classifications, 3 subjects | 2 source classifications retained; 3 subjects | The canonical generated document still identifies the primary class; it does not elevate the donor's heuristic discussion of a suspected secondary class into a model fact. |
| Knowledge retrieval on equivalent capture | Donor: 19 hits from 33 entries | RAGVIA: 14 deterministic results; 0 official TPU definitions; 13 source-attributed excerpts and 1 placeholder result | The primary class, all 3 subject name/code pairs, and all 13 movement kind/date/detail triples align. Fourteen result identities overlap; five donor-only results are classified below. The placeholder/source discrepancy is not presented as verified legal content. |
| Prompt | 11,993 user-prompt characters + 2,856 system-prompt characters | 18,098 user-prompt characters + 20,104 system-prompt characters | Prompt hashes and counts are in the private report. Neither prompt was sent. The donor prompt contains the source-derived text “Ignore a área”; RAGVIA excludes lexical annotations from the provider payload. This security divergence is intentional. |
| Template rendering shape only | 7,734 characters; 5 headings; 3 unique rendered dates | 5,890-character compatibility view; 6 headings; all 3 donor dates appear (RAGVIA has 4 unique dates) | The compatibility projection formats registry distribution dates as `dd/mm/yyyy` in `America/Sao_Paulo`; this fixed the measured ISO/local-date mismatch. It keeps separate representative and party sections and escaped deterministic markup. This is a formatting/template observation only, not generation parity. |
| Provenance/validation | No post-render factual/claim validation in the donor template path | 17 material claims, 45 evidence references; existing structured/factual/claim validators passed | The RAGVIA offline template candidate used the existing schema, validator, claim-evidence verifier and renderer. Its UUID references were local evaluation identifiers only, not persisted IDs. |
| External summary separation | Pipeline selects lawsuit records | Provider context contains normalized lawsuit data only | The captured external `summary` item was not included in either generation context. |
| Sensitive identifiers | No full source personal identifiers found in rendered output | No full source personal identifiers or representative registration found in either RAGVIA rendering | This is a content scan for this capture, not a general privacy proof. |

### Knowledge retrieval: exact result and interpretation

The differential uses the same capture SHA and equivalent normalized primary class, all 3 subject `(name, code)` pairs, and all 13 source-aligned movement records. The short-process path includes all movements. RAGVIA deliberately resolves only the primary class; the donor asks its KB for both classifications. RAGVIA used the production `_retrieve_supplemental_knowledge` helper with `EMBEDDING_SPACE_RUNTIME_ENABLED=false`, so its deterministic code/term path ran and semantic BGE was explicitly disabled; the donor used its local `hashing-4096` embedder. Scores from that hash embedder are not comparable to BGE cosine scores.

- Donor: 19 selected entries: 2 classes, 3 subjects, 9 movements, 5 concepts; 4 by code, 1 by exact term, 14 by hashing similarity. None of the 33 donor KB records declares a source URL/document in the loaded record.
- RAGVIA: 14 results: 1 primary class, 3 subjects, 6 movements, 4 concepts; 4 by supplemental code and 10 by exact term; 0 semantic BGE hits and 0 official TPU definitions. All rows carry document/reference/hash metadata, but one row (`assunto-899`) is a generic no-excerpt placeholder, not a source-attributed legal excerpt. “Code” here is the supplemental corpus key, **not** a claim that the mapping was verified by CNJ; each record remains marked `donor_mapping_verified_by_cnj=false`.
- Identity crosswalk: 14 donor hits have the same normalized kind/name-or-alias/code as a RAGVIA hit; RAGVIA-only hits: zero. The 5 donor-only IDs are `classe-embargos`, `mov-remessa`, `mov-audiencia-instrucao`, `mov-djen`, and `conceito-fase-inicial`.

| Common RAGVIA entries | Pinned source reference in the RAGVIA corpus | Difference from donor text |
|---|---|---|
| `classe-436` | Lei 9.099/1995, arts. 3º, 9º e 12-A | Donor gives an unsourced explanatory summary (including rito/limits); RAGVIA passes identified statutory text, not the donor prose. |
| `assunto-14033`, `assunto-10431` | CDC, inciso VI; Código Civil, art. 927 | Donor gives broad descriptions; the RAGVIA excerpts are limited to prevention/reparation wording and the statutory duty to repair damage caused by an ilícito. |
| `assunto-899` | Runtime row currently points only to the generic Código Civil URL | **Known repository inconsistency:** runtime value is the placeholder “Sem extrato abrangente...” with no article reference, while `docs/migrations/evidence/ragvia-m02-content-audit.json` records an `ADAPT_NARROW` decision and an Art. 1º excerpt/source reference. It is not counted as a verified legal excerpt merely because the row has a URL. No snapshot change was made: correcting this requires a versioned knowledge-corpus/provenance review. |
| `mov-distribuicao`, `mov-audiencia-conciliacao`, `mov-intimacao`, `mov-ato-ordinatorio`, `mov-juntada` | CPC arts. 285–286, 165, 269 and § 4º dos atos meramente ordinatórios | Donor prose adds purpose/status/party consequences; RAGVIA carries the cited provisions only. In particular, art. 165 does not itself establish all consequences of missing a JEC hearing. |
| `mov-100-digital` | Ato CNJ 3512, art. 1º | RAGVIA cites the implementation act; donor describes remote acts more broadly. |
| `conceito-polos`, `conceito-advogado`, `conceito-valor-causa`, `conceito-prazo-jec` | CPC arts. 319, 103, 291; Lei 9.099/1995, art. 12-A | RAGVIA gives source-bounded clauses; donor adds procedural role, OAB, competence/costs, and deadline explanations not fully contained in those selected clauses. The JEC business-day rule is the closest textual match. |

The five donor-only results are not five proven omissions: `classe-embargos` is the secondary ambiguous/suspect class; `mov-audiencia-instrucao` is a low-score near-neighbor to the conciliation event; `mov-remessa` has no accepted source-backed supplemental explanation in the M02 audit; `mov-djen` carries a universal deadline-start rule intentionally dropped because the governing regime matters; `conceito-fase-inicial` is a phase inference better left to directly injected registry metadata. The raw movements and class metadata remain available in RAGVIA's authorized process record; only their supplemental legal explanations are withheld.

The local deterministic RAGVIA template candidate is an **evaluation adapter**, not a production fallback or a provider implementation. It exercises existing validation/rendering components without persistence. It must not be used to claim Claude content or legal-quality parity. The private report records IDs, source references, hashes and counts, not capture text or generated summaries. `data/legal-knowledge/sources.json` records the public source URLs, retrieval timestamps and content hashes; that metadata was inspected, but it is not an independent check of present-day law, completeness, licensing or legal interpretation.

## `summary_ia` structure, information and safety

| Dimension | `rag_resumo` template | RAGVIA `juditSummary` projection | Result |
|---|---|---|---|
| Sections | Intro, `Partes`, `Classe`, `Assuntos`, `Movimentações` (5 headings) | Deterministic process header + synthesis, `Partes`, `Representantes`, `Classe`, `Assuntos`, `Movimentações`, current status, attention, plus populated conditional sections | Same core legal-document outline, with explicit RAGVIA-only representative/status/attention distinctions; not byte-identical. |
| Process identity | Donor intro uses parsed CNJ/registry fields | RAGVIA header projects authorized canonical identity and allowlisted registry metadata | RAGVIA identity is application-rendered and process-match checked before publication. |
| Parties | Donor flattens lawyer records into the party list and can render explanations | RAGVIA separates party and representative lists and only states a representative relationship when it matches a known party | Deliberate domain distinction; no party/representative flattening. |
| Movement / legal text | Donor template uses generated prose explanations | RAGVIA displays validated timeline/status/attention; supplemental law remains source-bounded and provenance-linked | No unsupported donor explanation is copied into legal facts. |
| Markup | Donor `sanitizar_inline` escapes values, allows a small exact inline-tag allowlist, drops unknown tags, and strips all tags on broken nesting | RAGVIA adapter emits fixed markup and HTML-escapes every dynamic value; no provider-authored tags enter this projection | Both constrain markup; RAGVIA path is deterministic. Counts on the provider-free rendering: donor 34 `<strong>`/9 `<li>`, RAGVIA 22 `<strong>`/39 `<li>`. |
| Privacy | Donor parser masks personal documents; this capture's rendered outputs contained no full source IDs | RAGVIA projection omits party documents and lawyer registration; the capture scan found no full source IDs or lawyer documents | RAGVIA is more restrictive for representative registration; capture scan is not a general privacy proof. |
| Existing contract | N/A | `iaSummary` remains unchanged; `juditSummary` is additive and null unless the existing persisted summary is publishable | PostgreSQL/API tests cover the same `iaSummary`, authorized response, and cross-tenant denial. |

The template rendering comparison is evidence about structure, escaping, and metadata formatting only. The measured text came from provider-free templates; it does **not** establish equivalence between Claude generations.

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
- **Deterministic retrieval comparison: measured, not full semantic parity.** On this capture, 14 normalized retrieval identities overlap and the five extra donor hits have the documented safety/precision reasons above. RAGVIA BGE/pgvector semantic fallback was not exercised; no claim about its live ranking follows from this run.
- **M02 source-artifact discrepancy: OPEN.** The runtime `assunto-899` placeholder conflicts with the M02 audit's recorded Art. 1º excerpt/source reference. This PR records the concrete mismatch but does not rewrite the versioned corpus; an owner-reviewed versioned correction/reindex decision remains needed.
- **Independent legal assessment: PENDING.** No jurist independently reviewed applicability, completeness, current validity, or comparative legal meaning of the excerpts/templates. The source manifest's captured HTTP/status/hash metadata is not that assessment.
- **Secret case: NOT exercised by this real capture.** Existing secret-summary tests and the deterministic local path remain the evidence for that boundary.
- **Frontend: existing browser renderer remains based on `iaSummary`.** The behavior test now proves the additive `juditSummary` wrapper does not replace or leak into the rendered/copy text. A live external consumer of the Judit compatibility item was not tested.

Do not describe this work as complete parser/knowledge/prompt/Claude parity. It demonstrates a real-capture, provider-free template comparison and an additive compatible output contract, with the stated differences preserved.
