# DataJud enrichment contract

Rpy treats DataJud as an optional official metadata enrichment source. Judit remains
the source for parties and movement text. DataJud may contribute class/TPU code,
subjects, adjudicating body and county when those values are present in the public
response.

## Activation boundary

The adapter is disabled by default.

Production activation requires all of the following:

- `DATAJUD_ENABLED=true`;
- `DATAJUD_AUTHORIZED_USE=true`, set only after deployment/product/legal review;
- `DATAJUD_API_KEY` containing the current public CNJ key;
- a metadata-only lookup purpose authorized for this deployment;
- no reliable internal indication that the requested process is restricted.

The key is intentionally not stored in source code or fixtures. A `401` or `403`
is returned to the pipeline as `auth_error` because the CNJ public key may rotate.
Transport failures, throttling and server failures are `unavailable`; they must
not invalidate the Judit version.

## Authenticated UI lookup path

The frontend submits `POST /processes/datajud/jobs` with `{"code": "<cnj>"}` and
a client-generated `Idempotency-Key`. The API authenticates the bearer/API key and
checks the deployment opt-in. It distinguishes `verified_public` (explicit,
consistent level zero in the authorized tenant's finalized source version) from
`unknown` (no reliable classification evidence). For a new independent lookup,
`unknown` is not converted to public: the fixed operation/purpose
`metadata_lookup` / `public_metadata_lookup` is recorded in the durable PostgreSQL
job and the opt-in is the deployment authorization for this restricted DataJud
metadata-only use. If persisted secrecy or any finalized source version reliably
indicates a positive secrecy level, the request is denied before enqueue. The
worker rechecks for newly known secrecy immediately before calling the adapter;
only a verified public classification is sent as level `0`, while unknown remains
`None`. The DataJud API's own filters or its response are never used as proof of
RagV's pre-query classification.

The API persists a `lookup_datajud_metadata` job in the PostgreSQL queue. The job
payload durably binds tenant, normalized CNJ, operation, fixed purpose, and the
classification evidence state at request time; idempotency remains tenant-scoped.
The UI polls `POST /processes/datajud/jobs/status` and can recover completed
metadata from `GET /processes/datajud/history`. Job payload and result are readable
only by the authenticated tenant that submitted that job. Replays return the same
job, while reusing a key for another CNJ is rejected. Job results follow the
configured queue retention policy. `/processos` aliases use the same contract.

For an existing process with reliable explicit public classification, the
verified-public path remains unchanged. A new CNJ, or an existing process whose
internal classification is unknown, may use only the separately authorized
metadata lookup. This path does not create a process record, does not assert a
public classification, and does not use Judit acquisition authorization. Known
restricted classifications are blocked, including a positive process secrecy
level or positive level in a finalized source record. Unknown classification is
therefore different from both public and known-secret; it is allowed only because
this operation requests limited metadata from the Public DataJud API under the
explicit environment opt-in. Deployments must keep the opt-in off unless this
specific purpose is compatible with their CNJ authorization.

Only workers receive `DATAJUD_API_KEY` and perform provider requests. The API
receives only the non-secret enablement/authorization flags and does not call the
DataJud adapter directly. Do not add API credentials or provider egress to the API
service to work around worker configuration; the provider-enabled worker network
boundary remains authoritative. If worker configuration is missing, the job fails
closed and the API returns a generic unavailable response.

## Current public API contract

Official example page checked on 2026-10-17: <https://datajud-wiki.cnj.jus.br/api-publica/exemplos/exemplo1/>.
Its TRF1 request uses `POST /api_publica_trf1/_search` and the body:

```json
{"query":{"match":{"numeroProcesso":"00008323520184013202"}}}
```

The adapter's `match.numeroProcesso` query and normalized 20-digit CNJ match this
example. The adapter additionally sends `size: 1` to bound the number of returned
hits; the official example omits that option, but it is a standard `_search` body
parameter. A synthetic transport test locks the example query and the `size: 1`
limit. No real provider request was made for this validation. The response field
`nivelSigilo` (if returned by DataJud) is not accepted as RagV's internal
classification evidence.

The CNJ public API uses tribunal-specific Elasticsearch-compatible endpoints:

```
POST https://api-publica.datajud.cnj.jus.br/api_publica_<alias>/_search
Authorization: APIKey <current public key>
Content-Type: application/json
```

Rpy queries the canonical 20-digit process number and requests at most one hit.
The tribunal alias is derived from the CNJ `J.TR` fields for supported segments.
Unsupported segment/TR combinations fail closed and do not fan out across aliases.

Relevant official/current references:

- https://www.cnj.jus.br/sistemas/datajud/api-publica/
- https://atos.cnj.jus.br/atos/detalhar/6972
- https://www.cnj.jus.br/programas-e-acoes/numeracao-unica/perguntas-frequentes/
- https://atos.cnj.jus.br/atos/detalhar/119

## Use restrictions

Portaria CNJ n. 374/2026 amended the public DataJud rules. The reviewed text states
that public API/open-data consumption is subject to legal, non-commercial and
authorized use, prohibits modification/distribution/sale or commercial exploitation
of the supplied data, and requires attribution to CNJ/DataJud in publications,
applications and studies.

For that reason, presence of the adapter in Rpy is **not** authorization to enable
it in every deployment. The explicit `DATAJUD_AUTHORIZED_USE` gate exists so
commercial or otherwise incompatible environments cannot enable DataJud merely by
setting a technical API key.

## Data minimization and secrecy

- A known positive secrecy level is denied before enqueue/provider execution. `None` at the adapter boundary means unknown classification, never a public classification.
- Raw DataJud HTTP payloads are not persisted by this adapter.
- The normalized merge contract stores only modeled metadata/provenance.
- Parties and movement text are never sourced from DataJud by this enrichment path.
- Provider credentials must never be logged or added to audit metadata.

## Best-effort behavior

DataJud enrichment is supplementary. `disabled`, `not_found`, `auth_error` and
`unavailable` do not erase, duplicate or invalidate a valid Judit process version.
Conflict handling remains deterministic: when official metadata is available,
DataJud wins only for the modeled metadata fields and the Judit/DataJud disagreement
is persisted and surfaced in `Pontos de atenção`.
