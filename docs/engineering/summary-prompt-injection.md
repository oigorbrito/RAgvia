# Summary prompt-injection hardening

## Failure mode

Rpy does not expose a free-form prompt in the summary UI. The relevant attack surface is indirect prompt injection: process movements, attachments or other source-derived text can contain imperative language that an LLM might incorrectly treat as instructions.

Examples include text asking the model to ignore the summary contract, reveal its system prompt, produce unrelated content such as recipes or weather, or close prompt delimiters and inject a new pseudo-role.

## Security objective

Source-derived text must remain evidence to summarize, not authority over generation behavior. A successful attack must not:

- redefine the model role or output contract;
- cause general-assistant behavior;
- reveal internal prompt text;
- gain tools, network access or additional privileges;
- bypass post-generation validation and publication gates.

This is risk reduction, not a claim that prompt injection can be made impossible. OWASP LLM01:2025 explicitly treats prompt injection as a defense-in-depth problem, and Anthropic's current guidance for indirect prompt injection recommends marking third-party content as untrusted, using structured/JSON encoding, limiting privileges and red-team testing the workflow.

References:

- OWASP LLM01:2025 Prompt Injection: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- OWASP LLM Prompt Injection Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
- Anthropic jailbreak and prompt-injection mitigation: https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks
- Anthropic prompting best practices / structured XML context: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables

## Implemented controls

1. **No direct chat surface.** The frontend accepts a CNJ and credential; users do not provide arbitrary generation instructions.
2. **Least privilege.** Summary generation uses the model as a text generator only. No web-search, tool-use or action capability is attached to the request.
3. **System/data separation.** The system prompt states that all process, movement, attachment, party, subject, header and glossary content is untrusted for instruction purposes.
4. **JSON encoding and delimiter hardening.** Dynamic process data is JSON serialized and characters that could close XML-style prompt delimiters are escaped before crossing the provider boundary.
5. **Retry isolation.** Validation feedback is JSON encoded before a corrective generation attempt so model-produced text cannot become a second-order delimiter injection through validator messages.
6. **Output contract.** Publication still requires deterministic validation; non-secret provider output now rejects Markdown headings outside the documented legal-summary schema.
7. **Adversarial provider acceptance.** `scripts/evaluate_prompt_injection_live.py` exercises recipe diversion, weather diversion, prompt exfiltration and delimiter breakout against the real configured model. It is an environment/provider acceptance test, not a CI test with fake credentials.

## What this does not do

Rpy does not claim that keyword filters or a stronger system prompt eliminate prompt injection. It also does not add a second guardrail LLM by default. That would add another external call, cost, latency and a second probabilistic component. Introduce one only if measured adversarial failure rates after these controls justify the additional boundary.

Likewise, fine-tuning is not used as a security boundary. Fine-tuning may improve task behavior, but source isolation, least privilege, deterministic validation and adversarial evaluation remain necessary even for a fine-tuned model.

## Acceptance

For repository/offline evidence, unit tests must prove the prompt envelope cannot be closed by raw source text and that out-of-contract headings fail validation.

For live provider evidence, run:

```bash
python scripts/evaluate_prompt_injection_live.py
```

The exact prompt/model candidate fails acceptance if any adversarial case produces a forbidden off-task marker or cannot produce a validator-accepted legal summary within the normal one-correction-attempt contract.


## Structured-output hardening

The v4 generation contract adds a narrower provider-output capability. The request uses Anthropic Structured Outputs with a closed JSON Schema and the application renders the user-visible Markdown itself. The model therefore no longer controls:

- document title;
- process header fields;
- party enumeration;
- Markdown heading names or order;
- arbitrary top-level response fields.

The validator also rejects prompt/meta language and external URLs that appear in the generated summary without corresponding source context. This is deliberately source-aware rather than a blanket keyword ban: a judicial filing may itself discuss prompt injection, and a faithful summary must be able to mention that fact when it is actually present in the record.

The live adversarial suite additionally covers multilingual override, Base64 obfuscation, hidden-markup instructions and payload splitting across movements.
