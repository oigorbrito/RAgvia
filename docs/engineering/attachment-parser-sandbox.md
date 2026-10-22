# Attachment parser sandbox

Binary attachment parsing is isolated from the main worker runtime.

## Trust boundary

PDF, PDFium/Pillow rasterization and Tesseract OCR run in the `attachment-parser` sidecar. Workers send already-authorized bytes over a Unix domain socket at `/run/rpy-parser/parser.sock`. The socket lives on a shared tmpfs volume; document bytes are framed in memory and are not written to the socket volume.

The parser service:

- uses `network_mode: none`;
- receives only attachment parser/OCR settings, never database or external-provider credentials;
- runs as uid/gid 10001 with a read-only root filesystem, all Linux capabilities dropped and `no-new-privileges`;
- keeps writable parser scratch space on private `/tmp` tmpfs;
- is bounded to 0.75 CPU, 768 MiB memory and 64 PIDs;
- enforces a server-side wall-clock timeout for each Unix-socket request, including frame reads;
- retains the application byte limit, PDF OCR page limit and Tesseract timeout;
- applies its own configured byte/chunk limits even if a worker requests looser values.

The worker applies an independent wall-clock timeout to the complete Unix-socket request. The parser also applies its own request timeout so a stalled client cannot retain a server task indefinitely. Socket absence, timeout, malformed protocol responses and parser failures remain attachment-local `unreadable`/`corrupt` states.

## Data handling

Only authorized attachment bytes cross the parser socket. The response contains normalized chunk text and page/character metadata. Raw bytes are not logged or persisted by the protocol. Tesseract input uses a private temporary directory inside the parser's ephemeral `/tmp` tmpfs and is deleted when the call returns.

## Operational contract

Production Compose validation fails if the parser gains Docker network access, receives provider/database secrets, loses runtime confinement, changes resource budgets, loses its positive request timeout, stops using the tmpfs-backed Unix socket, or if either worker stops waiting for parser health. The socket itself is chmod `0600`; both containers deliberately run as UID 10001.

The production image includes the OCR runtime dependencies (`pypdfium2`, Pillow, Tesseract and Portuguese language data) so the isolated OCR path is executable when `ATTACHMENT_OCR_ENABLED=true`.

Synthetic corrupt PDF/image cases and Unix-socket timeout/unavailability tests are provider-free and run in CI.
