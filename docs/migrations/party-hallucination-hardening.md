# Party hallucination hardening

## Scope

Strengthen post-generation validation without adding an NLP/NER dependency.

## Imported pattern

None. This change is domain-specific validation code for Rpy.

## Detection boundary

Only text that explicitly assigns a procedural role is treated as a party assertion. Supported roles include autor/autora, réu/ré, requerente/requerido, exequente/executado. Both role-before-name and name-before-role forms are covered.

Names of lawyers, witnesses, judges or other people are not rejected merely because they are absent from the process party list.

## Name comparison

Party names are normalized for Unicode diacritics, case, whitespace and punctuation before comparison. This avoids false hallucination errors caused only by formatting differences such as `Joao` versus `João`.

## Tests

Coverage includes free prose role assertions, copula forms, inverted role forms, accepted known parties, diacritic variation, and named non-parties.
