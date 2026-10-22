# Bolt's Journal

## 2026-09-24 - Memoize character property lookups in unicode_security
**Learning:** Character-by-character `unicodedata` lookups (`unicodedata.category` and `unicodedata.name`) in text normalization and security verification pipeline create significant CPU bottlenecks on large text chunks. Memoizing pure single-character helper functions with `functools.lru_cache(maxsize=1024)` yields ~2.8x execution speedup.
**Action:** Always check for character-by-character C-extension or stdlib lookups in text transformation pipelines and apply bounded `lru_cache`.
