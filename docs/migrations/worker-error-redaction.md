# Worker error redaction

Worker task failures are persisted in `jobs.error_log` and emitted to application logs, so raw exception strings are treated as untrusted diagnostic data.

The worker now sanitizes exception messages before either sink. The sanitization removes configured provider/webhook/ops/database secrets, credentials embedded in URLs, bearer credentials, quoted or unquoted common token/password key-value forms, and recognized standalone `sk-...` API-token families. Property-based tests vary structured key/value quoting and generated token bodies so these claims remain reproducible without live credentials. Persisted messages are bounded to 2,000 characters.

Worker failures no longer use `logger.exception()`: Python traceback rendering repeats the original exception message and can bypass message-level redaction. Operational logs retain job id, failure class, permanent/retryable classification, and the sanitized bounded detail.

This does not make arbitrary payload logging acceptable. New task/provider code must continue to avoid placing prompts, raw webhook bodies, parties, movement text, credentials, or database URLs into exception messages or explicit log fields.
