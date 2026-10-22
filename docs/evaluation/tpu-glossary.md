# TPU glossary source contract

## Purpose

Rpy resolves process class/subject codes against a pinned local snapshot of the
CNJ Tabelas Processuais Unificadas (TPU). Runtime generation must not query an
external terminology service and must not infer a definition by name when a code
is absent from the pinned snapshot.

## Authoritative source

Publisher: Conselho Nacional de Justiça (CNJ).

Public entry point:

- https://www.cnj.jus.br/tabela-processuais-unificadas/

The CNJ public TPU system states that tables are continuously maintained, exposes
previous versions, downloadable Excel/SQL artifacts and a public API/WebService.
The public class/subject pages observed while preparing this snapshot reported TPU
version `12/09/2026`.

Version/download pages:

- https://www.cnj.jus.br/sgt/versoes.php
- https://www.cnj.jus.br/sgt/versoes_anteriores.php
- https://www.cnj.jus.br/sgt/infWebService.php

The repository snapshot is `data/tpu/2026-09-12.json`. It is deliberately small:
it proves the versioned-source/runtime contract without vendoring the complete CNJ
dataset before redistribution terms have been reviewed.

## Licensing / redistribution status

The CNJ pages above provide public consultation, a public WebService and versioned
Excel/SQL downloads. CNJ Portaria 209/2019 defines open data as public,
machine-processable data published under an open license that permits free use,
consumption or combination with source attribution. Resolução 333/2020 uses the
same open-license concept for judicial open data.

Those general policies strengthen the basis for reuse, but the TPU download pages
reviewed for this project still do not attach a dataset-specific license notice to
the complete TPU dump. Rpy therefore does **not** claim that mirroring the entire
official dump inside this repository is expressly authorized.

To avoid conflating public access with repository redistribution, the complete
catalog may be prepared locally from an operator-obtained official export without
committing that source artifact. The runtime remains offline and the repository
continues to vendor only the minimal reviewed snapshot until dataset-specific
redistribution terms are confirmed.

Relevant CNJ policy sources:

- https://atos.cnj.jus.br/atos/detalhar/busca-atos-adm?documento=3140
- https://atos.cnj.jus.br/atos/detalhar/3488

## Snapshot schema

Each file contains:

- `schema_version`: Rpy's local snapshot schema;
- `tpu_version`: immutable snapshot identifier (`YYYY-MM-DD` for CNJ releases);
- publisher/source/version metadata;
- `entries[]`, keyed by `(kind, code)` where `kind` is `class` or `subject`;
- source name, normalized definition and source reference for every entry.

Unknown codes are omitted. The resolver never uses fuzzy/name matching and never
falls back to network access.

## Update procedure

1. Check the CNJ version/download page and record the published TPU version.
2. Obtain the official export/API data outside the repository and review the
   redistribution terms applicable at that time.
3. Normalize the entries you are authorized to use into a local UTF-8 CSV with
   exactly these required columns: `kind,code,name,definition,source_ref`.
   `kind` is only `class` or `subject`; numeric codes remain strings.
4. Build a **new** snapshot without network access:

   ```bash
   python scripts/build_tpu_snapshot.py \
     --input /secure/local/tpu-normalized.csv \
     --output /secure/local/2026-09-12.json \
     --tpu-version 2026-09-12 \
     --source-version-label 12/09/2026
   ```

   The builder validates required fields/duplicates, sorts entries
   deterministically, records the local input SHA-256 and refuses to overwrite an
   existing snapshot.
5. Review the generated JSON against the official source. Copy it to
   `data/tpu/<version>.json` only when the intended redistribution/deployment use
   is authorized. Never commit the original Excel/SQL/API dump merely because it
   was publicly downloadable.
6. Preserve source references and do not invent explanatory text absent from the
   reviewed source.
7. Add/update tests that resolve known codes, omit unknown codes and prove the
   selected snapshot version is present in generated context metadata.
8. Run the complete Rpy CI, including offline release smoke. Runtime must remain
   provider-free for glossary resolution.

## Current integration boundary

This change establishes the local versioned catalog and deterministic resolver.
Injection into the RAG provider context and PostgreSQL integration evidence are a
separate #138 block. Secret-process context must continue to bypass external
providers regardless of glossary availability.
