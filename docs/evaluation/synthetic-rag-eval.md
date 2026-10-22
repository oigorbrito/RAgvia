# Synthetic RAG evaluation baseline

This evaluation is a provider-free baseline for the iaSummary quality work tracked in #115. It is intentionally narrower than a legal-quality guarantee.

## Dataset

`tests/eval/synthetic_cases.json` contains 30 manually labelled synthetic cases. The corpus contains no real case data and no intentionally included personal identifiers. Coverage includes civil, labour, criminal and tax-enforcement proceedings; first and second instance; closed proceedings; secrecy; more than 200 movements; empty steps; and the inconsistency categories listed in #115.

Each case stores the expected labels and a synthetic candidate snapshot. The candidate annotations are versioned so metric changes remain reproducible and reviewable in Git.

## Metrics

`scripts/evaluate_synthetic_rag.py` computes four aggregate metrics and the same metrics per case:

- `unsupported_assertion_rate`: unsupported labelled claims divided by all candidate claims;
- `milestone_recall`: expected process milestones recovered by the candidate divided by all expected milestones;
- `inconsistency_detection_recall`: expected inconsistencies detected by the candidate divided by all expected inconsistencies;
- `attention_false_positive_rate`: predicted `Pontos de atenção` items without a corresponding expected attention label divided by all predicted attention items.

For the initial 30-case baseline the versioned snapshot scores:

- unsupported assertion rate: `0.05`;
- milestone recall: `0.90`;
- inconsistency detection recall: `17/18` (`~0.9444`);
- attention false-positive rate: `1/19` (`~0.0526`).

These values are descriptive baseline measurements, not claims about production legal accuracy.

## Versioned non-regression gate

`tests/eval/baseline.json` records the reviewed baseline. It contains:

- a dataset version plus the exact Git blob SHA of the dataset;
- the configured model and prompt version;
- retrieval parameters that materially affect candidate selection;
- aggregate non-regression thresholds;
- per-case defaults and explicit exceptions matching the measured baseline.

The initial threshold policy is deliberately conservative: **no metric may become worse than the reviewed baseline**. No extra universal safety margin is invented. A better result passes without requiring a baseline update; changing the dataset, model, prompt version or recorded retrieval configuration requires an explicit baseline update in the same reviewed change.

Per-case failures identify the `case_id` and metric that regressed. Aggregate failures identify the metric and documented threshold. This makes the CI failure attributable rather than a single opaque score.

Run the gate locally with:

```bash
python scripts/evaluate_synthetic_rag.py --check-baseline
```

The CI runs the same command with provider credentials blank. A non-zero exit is produced only when the versioned baseline contract is violated.

## Reproduction

Run the descriptive report without the gate:

```bash
python scripts/evaluate_synthetic_rag.py
```

The evaluator uses only local repository data. Unit coverage blocks socket connection attempts while both the scorer and baseline comparison run. Judit, Anthropic, OpenAI and Gemini are not required or contacted by this evaluation.

## Limitations and next work

The current corpus still scores versioned synthetic candidate annotations. The non-regression gate therefore protects this reviewed synthetic baseline but does not yet execute the full offline generation pipeline against each case and cannot by itself measure end-to-end generation quality.

The next #115 block is to derive candidate outputs from the provider-free application pipeline while preserving deterministic execution, then extend the baseline to retrieval precision/relevance, source presence and restricted-content leakage using those actual pipeline outputs. Only then should metric deltas be interpreted as broader implementation-quality evidence rather than changes to the manually versioned candidate snapshot.
