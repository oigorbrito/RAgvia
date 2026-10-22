# Adversarial property fuzzing

Rpy combines fixed regression attacks with property-based mutation around the summary boundary. The goal is not to prove prompt injection absent; it is to continuously explore combinations that fixed examples do not cover while keeping repository CI deterministic and provider-free.

## Offline contract

The synthetic corpus is stored in `scripts/adversarial_summary_corpus.json`. It contains no real process data. `app/adversarial_mutations.py` applies composable mutations for casing, spacing, markup, delimiter nesting and Unicode zero-width/bidi/homoglyph variants.

`tests/test_adversarial_property_fuzzing.py` uses Hypothesis to generate mutation combinations across:
- process movement/header input;
- attachment input;
- structured provider output fields;
- direct publication-validation attempts.

The enforced properties are:
- source data cannot close the serialized prompt envelope because prompt JSON escapes `<`, `>` and `&`;
- Unicode controls remain subject to the existing model-view hardening;
- attachment mutations remain data and do not mutate the source fixture;
- provider-authored strings cannot create new Markdown sections through the closed schema/application renderer;
- direct generated Markdown with an unapproved heading cannot pass the publication validator;
- webhook URL credentials are removed from ASGI `path`/`raw_path` for generated opaque token values before application logging;
- malformed webhook paths never gain request-local authentication state merely because redaction recognized the prefix.

`scripts/project_harness.py` requires both this document and the canonical `--hypothesis-seed=20260918` CI wiring, so adversarial fuzzing cannot silently disappear from the release evidence. Main CI passes that seed so a failing generated sequence is reproducible. Hypothesis also shrinks failing inputs. If a new minimized failure is discovered, add the minimized synthetic input to `scripts/adversarial_summary_corpus.json` before fixing the defect so it becomes a permanent regression fixture. Do not retain `@reproduce_failure` blobs as permanent evidence because Hypothesis does not guarantee those blobs across versions.

The dependency is development-only and pinned in `requirements/constraints.txt`.

## Live provider sampling

`scripts/evaluate_prompt_injection_live.py` consumes the same corpus and applies a bounded deterministic mutation to each case before calling Anthropic. Its JSON report records:
- exact prompt version;
- configured generation model;
- per-case result;
- attack family;
- attempts and validator result;
- forbidden-marker hits;
- aggregate pass/total/pass-rate by family.

`.github/workflows/adversarial-live.yml` is scheduled weekly and is also manually dispatchable. It runs only when `ANTHROPIC_API_KEY` has actually been provisioned to GitHub Actions; absence of the credential is reported as a notice and is not represented as a successful live acceptance.

A live failure is an acceptance blocker until triaged or explicitly risk-accepted through the normal external change/risk process. Repository code must not weaken the validator or delete a regression case merely to make the live suite pass.

## References

- Hypothesis reproducibility/settings: https://hypothesis.readthedocs.io/en/latest/settings.html
- Hypothesis failure replay: https://hypothesis.readthedocs.io/en/latest/tutorial/replaying-failures.html
- OWASP Prompt Injection Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
