# Bearer token configuration validation

`RPY_BEARER_TOKENS` is parsed and validated during API startup. The configuration must contain at least one token, and token keys must not contain whitespace.

The request parser strips transport whitespace around the bearer credential before constant-time comparison. Accepting leading, trailing, or embedded whitespace in configured token keys would therefore create credentials that are impossible or intermediary-dependent to present over HTTP. Such configurations now fail startup instead of leaving a healthy-looking API with unusable authentication.

Token rotation is unchanged: multiple distinct non-whitespace tokens may map to the same tenant UUID during an overlap window.
