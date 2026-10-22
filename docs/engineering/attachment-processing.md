# Attachment processing boundary

Issue #137 introduces attachment support in staged blocks. This document defines the processing contract before downloader activation; local parsing and optional image OCR remain provider-free.

## Phase 1: durable state foundation

This phase stores only attachment metadata, processing state and normalized text chunks. It does **not** enable Judit attachment download and keeps `with_attachments: false` until the source download contract is implemented and tested.

Attachment states are:

- `pending`: known attachment whose bytes have not completed local processing;
- `ready`: local parsing/chunking completed and chunks are eligible for retrieval;
- `unavailable`: the authorized source could not provide the attachment;
- `corrupt`: bytes were obtained but fail format/integrity parsing;
- `unreadable`: supported content was obtained but no usable text could be extracted, including OCR failure.

A failure state is attachment-local. It must not delete movements, invalidate other attachments, or make a process summary unusable by itself.

## Phase 2: bounded local text processing

The first local parser accepts only source bytes already obtained through an authorized caller and identified as `text/plain` (content-type parameters such as `charset=utf-8` are normalized away). It does not fetch URLs and does not retain raw bytes.

Runtime limits:

- `ATTACHMENT_MAX_BYTES`, default 10 MiB;
- `ATTACHMENT_CHUNK_CHARS`, default 4000 and minimum 256 characters.

UTF-8 text is decoded locally, CRLF/CR line endings are normalized to LF, empty text is rejected, and NUL/invalid UTF-8 bytes are classified as `corrupt`. Oversized, empty or unsupported content is classified as `unreadable`. The SHA-256 stored on `process_attachments` is calculated from the received bytes; per-chunk SHA-256 continues to be calculated from normalized chunk text.

Text chunking is deterministic and non-overlapping. It prefers a whitespace/newline boundary in the latter half of the configured chunk window and stores normalized character positions as half-open `[char_start, char_end)` offsets. Reprocessing the same source attachment replaces chunks atomically through the Phase 1 persistence primitive.

## Phase 3: PDF text-layer processing

`application/pdf` bytes already obtained by an authorized caller are parsed locally with `pypdf`. The same byte limit is applied before parsing. The parser requires a PDF header and does not fetch fonts, images, models or any other network resource.

For each page with extractable text:

- line endings are normalized;
- text is chunked with the same deterministic non-overlapping algorithm;
- `page_start`/`page_end` identify the one-based source page;
- `char_start`/`char_end` are half-open offsets inside that normalized page text.

Malformed/non-PDF bytes are `corrupt`. Password-protected PDFs are `unreadable` because Rpy has no authorized password contract. PDFs with no extractable text layer are `unreadable` with `pdf_text_unavailable`; they are not silently treated as empty or sent to an external service. That state is the explicit handoff point for the future local OCR path.

`pypdf` is pinned through the repository dependency lock. Raw PDF bytes are never persisted by this layer.

## Phase 4: authorized retrieval and summary provenance

Ready attachment chunks participate in generation only when the summary job can resolve its originating tenant from the unique `judit_request_id` and that tenant is authorized for the process through `tenant_processes`. Old/internal jobs without an authorized tenant continue without attachment context rather than bypassing the authorization boundary.

Retrieval is provider-free and lexical in this phase:

- `attachment_chunks` has a PostgreSQL GIN index over `to_tsvector('portuguese', text)`;
- tenant, process, current version, `ready` state and `secrecy_level=0` are applied inside the authorized candidate CTE before `ts_rank_cd` ranking;
- the query combines the normal RAG retrieval terms with available process class/subject terms;
- `ATTACHMENT_RETRIEVAL_LIMIT` defaults to 12 chunks;
- `PROVIDER_ATTACHMENT_TEXT_MAX_CHARS` defaults to 20,000 total characters and bounds the attachment text sent to the generator;
- no attachment embedding provider or reranker is introduced by this phase.

The provider receives only the selected bounded attachment context plus aggregate attachment state counts. Secret processes return before any attachment query or provider access.

Exact used chunks are persisted in `process_summary_attachment_sources`. Provenance contains only identifiers, source attachment id, page/character positions, SHA-256 and source order — never chunk text, document bytes, provider prompts or responses. `/v1/.../fontes` can read this safe provenance without granting the API role access to `attachment_chunks` or `process_attachments`.

## Phase 6: optional local OCR for images

Rpy supports an **opt-in local** OCR path for already-authorized `image/png` and
`image/jpeg` bytes using a locally installed Tesseract 5.x executable. Tesseract
is Apache-2.0 licensed and uses Leptonica for image input. The standard offline
Rpy image does not install OCR binaries or language packs, and CI does not download
them.

Activation is explicit:

- `ATTACHMENT_OCR_ENABLED=true`;
- `ATTACHMENT_OCR_BINARY`, default `tesseract`;
- `ATTACHMENT_OCR_LANGUAGE`, default `por`;
- `ATTACHMENT_OCR_TIMEOUT_SECONDS`, default 30.

When enabled, Rpy validates byte limits and PNG/JPEG magic before invoking the
engine. The authorized image bytes are written only to a private temporary
directory for the subprocess invocation and are deleted when the invocation exits;
they are never written to the database or application logs. Tesseract output is
captured as UTF-8 text, normalized and chunked through the same deterministic
non-overlapping path used for plain text.

Failure classification is attachment-local:

- OCR disabled: `unreadable/ocr_disabled`;
- binary/language environment unavailable: `unreadable/ocr_unavailable` or `ocr_failed`;
- timeout: `unreadable/ocr_timeout`;
- invalid image magic: `corrupt/invalid_image_header`;
- no recognized text: `unreadable/ocr_no_text`.

Image-only/raster PDF uses a separate optional local rasterization layer when OCR
is enabled. Rpy uses `pypdfium2`/PDFium to render each page to PNG in memory and
passes that PNG to the same local Tesseract adapter. `pypdfium2` is installed
only by the `ocr` extra (and development tests); the standard runtime image does
not include PDFium or Pillow.

PDF OCR controls:

- `ATTACHMENT_PDF_OCR_SCALE`, default 2.0 (144 DPI relative to PDF's 72 DPI base), required to be finite and greater than zero;
- `ATTACHMENT_PDF_OCR_MAX_PAGES`, default 100.

Production preflight validates the OCR boolean/numeric controls before deployment; non-finite raster scales such as `NaN`/`Infinity` are rejected both by preflight and runtime rather than reaching PDFium.

When OCR is disabled, PDFs without a text layer keep the historical
`unreadable/pdf_text_unavailable` result. When OCR is enabled, a page-count limit
is checked before Tesseract runs. Rasterizer absence/failure remains attachment-local
and cannot invalidate process movements.

Official implementation references reviewed for this phase:
- Tesseract 5.x command-line documentation;
- Tesseract Apache-2.0 license / Leptonica image-input boundary.

## Supported local processing

The implemented parsing targets are:

1. PDF documents with an extractable text layer — Phase 3;
2. UTF-8 plain text — Phase 2;
3. PNG/JPEG images through optional local Tesseract OCR — Phase 6;
4. raster/image-only PDF through optional local PDFium rendering followed by the
   same Tesseract OCR path.

The OCR extra currently pins `pypdfium2 5.13.x` and Pillow 12.3.x. pypdfium2 is
Apache-2.0/BSD-3-Clause and bundles/uses PDFium under a BSD-style license plus
dependency licenses; deployments that redistribute the OCR extra must retain the
license notices shipped by its wheel. No network-dependent model download may be
added to CI or the standard offline image.

## Chunking contract

Chunks are attachment-specific and retain:

- process id and process version id;
- attachment id and stable source attachment id;
- zero-based chunk order;
- optional page start/end;
- optional character start/end;
- SHA-256 of normalized chunk text.

Page and character bounds are paired and monotonic. Reprocessing an attachment atomically replaces its chunks, so retries and source redelivery cannot accumulate duplicate chunks. Overlap is not assumed by default; a parser may introduce bounded overlap only when the document structure/benchmark justifies it.

## Security and tenancy

Attachment retrieval is fail-closed:

- tenant authorization is checked through `tenant_processes`;
- process id and current version id must match before any chunk reaches RAG context;
- only attachments in `ready` state are retrievable;
- secret processes (`secrecy_level > 0`) never return attachment text to the external-provider path;
- the foundation stores no provider response payload and no raw attachment bytes;
- fixtures and logs must use synthetic text and identifiers only.

A later downloader must fetch bytes only from the source authorized for the process/tenant and must never log document bodies, signed download URLs, credentials or raw provider payloads.

## Phase 5: public status signals

Attachment processing state is surfaced without granting the API direct access to attachment rows or chunk text.

- `process_attachment_status_counts` exposes only per-process/per-version aggregate counts for `pending`, `ready`, `unavailable`, `corrupt` and `unreadable`;
- public `/v1` responses merge those counts under `flags.attachments`, including `total`, `processing_complete` and `degraded`;
- no attachment id, source id, chunk id, raw text, byte hash or provider payload is exposed through this status surface;
- generation converts non-ready/error counts into deterministic factual warnings that validation requires inside the summary's `Pontos de atenção` section;
- `ready` alone does not create a warning; one failed attachment does not invalidate otherwise valid movement/context data;
- secret-process generation still returns before external-provider attachment retrieval.

## Judit source manifest

When a non-secret Judit lawsuit payload contains an `attachments` array, Rpy normalizes only the minimal source manifest needed for durable identity and later processing:

- `attachment_id` (required for a manifest entry);
- source attachment name, preferring `attachment_name` and falling back to `content`;
- source attachment date when parseable;
- source provider status when present.

Arbitrary provider fields, signed/download URLs and credentials are not copied into the normalized manifest. Duplicate source ids are collapsed deterministically. Secret processes discard the manifest in the promotable structure.

The manifest is part of semantic version identity because adding/removing/changing a source attachment can change the evidence available to a future summary. Local processing status (`pending`, `ready`, `unavailable`, `corrupt`, `unreadable`) is deliberately not part of that fingerprint. New manifest rows start as `pending`; re-syncing source metadata does not reset an already-processed row.

## Judit authenticated download boundary

Rpy now has a provider-boundary primitive for the documented Judit lawsuit attachment endpoint. It downloads by CNJ, instance and attachment id using the configured `JUDIT_API_KEY`, applies the same `ATTACHMENT_MAX_BYTES` ceiling before returning bytes to local processing, normalizes only the response content type, and never logs or persists provider response bodies, signed URLs or credentials.

This boundary is intentionally **not** wired to automatic acquisition yet. `with_attachments` remains `false` in lawsuit requests, so this change cannot introduce attachment charges or alter release behavior by itself. Automatic acquisition still requires an explicit rollout that parses Judit attachment metadata, preserves the secret/private-document boundary, sequences download/processing before summary publication where required, and proves the flow with synthetic integration coverage.

The authenticated attachment download rejects HTTP redirects before issuing another request. This is deliberate: automatic redirect handling could forward the Judit `api-key` to a destination outside the fixed provider endpoint.

Current Judit materials agree on `with_attachments: true`, attachment ids and CNJ/instance addressing, but currently expose more than one download/authentication shape. Automatic acquisition therefore remains gated until one deployment-authoritative contract is selected and tested. Provider availability, commercial authorization and private-document credentials remain deployment concerns rather than assumptions in code.

## Controlled Judit acquisition

Automatic source acquisition is wired behind two deployment gates and is disabled by default:

- `JUDIT_ATTACHMENTS_ENABLED=true`;
- `JUDIT_ATTACHMENT_DOWNLOAD_MODE=direct_api_key`.

When both are explicit, new Judit requests and trackings set `with_attachments: true`. For a promoted, non-cached, non-secret version with a non-empty attachment manifest, finalization enqueues one `process_judit_attachments` job instead of generating the summary immediately.

The acquisition job operates only on the current finalized version. It downloads each local `pending` attachment through the bounded Judit client, processes bytes locally, and enqueues `generate_process_summary` only after the version has no remaining `pending` attachments. Retries are idempotent because already-processed rows are not selected again.

A deterministic provider rejection (normal 4xx other than 408/429) becomes attachment-local `unavailable/judit_download_rejected`, allowing the process summary to continue with the existing degraded-status warning. Timeouts, transport failures, 408, 429 and 5xx remain ambiguous and fail the acquisition job so the durable queue can retry instead of silently discarding a document.

The configured mode is intentionally explicit because Judit's current official materials are inconsistent: the general authentication/quickstart documentation specifies `api-key` for all API modules, and current 2026 integration examples show direct lawsuit attachment download with `api-key`, while the attachment-specific API-reference page still shows a Bearer-token endpoint returning an `attachment_url`. Rpy therefore does not silently select a mode when attachment collection is enabled.

## Judit activation gate

`with_attachments` remains `false` until all of the following exist in one tested rollout:

- an authenticated/authorized download contract;
- local byte-size/content-type limits;
- parser dispatch and failure classification;
- idempotent state/chunk persistence;
- secret-process short-circuit;
- synthetic integration coverage proving failed attachments do not damage valid process data.
