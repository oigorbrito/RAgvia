# Provider context safety envelope

External generation now has a deterministic character budget before any Anthropic request is sent.

## Limits

Workers read three positive integer settings:

- `PROVIDER_STEP_TEXT_MAX_CHARS` (default `12000`): maximum text retained from any one selected movement.
- `PROVIDER_STEPS_TEXT_MAX_CHARS` (default `80000`): aggregate text budget across all selected movements.
- `PROVIDER_PROMPT_MAX_CHARS` (default `120000`): hard cap for the complete user prompt, including process metadata, selected movements and validation-correction instructions.

The per-step limit must not exceed the aggregate movement budget, and the aggregate movement budget must be lower than the final prompt cap. Workers validate this relationship at startup.

These are character limits, not model token estimates. Their purpose is a deterministic application-side safety/cost envelope that does not depend on provider tokenization details.

## Movement preservation

Retrieval ranking is unchanged. The same selected movements remain represented in the provider payload. Individual movement text is truncated deterministically when necessary; if the selected texts still exceed the aggregate budget, the remaining budget is distributed across the selected movements rather than silently dropping movement records.

Step number, date and title are preserved.

## Hard failure before provider call

After the final user prompt is assembled, the application checks `PROVIDER_PROMPT_MAX_CHARS`. If process metadata, parties, subjects or the validation-correction retry still make the prompt exceed the hard cap, generation fails locally before the Anthropic operation is invoked.

The application deliberately does not silently truncate parties or process metadata to fit the prompt. Dropping those facts could weaken party-hallucination validation or materially alter the procedural record.

Secret proceedings are unaffected because their deterministic local summary path does not invoke the external provider.
