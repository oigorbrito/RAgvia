from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.claim_verification import ClaimVerification, VERIFICATION_STATUSES
from app.json_utils import decode_json_list, decode_json_object
from app.summary_output import (
    structured_summary_document_is_canonical,
    structured_summary_document_matches_process,
)

_EVIDENCE_REF_RE = re.compile(r"^[pma]-[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_ID_RE = re.compile(
    r"^(?:synthesis|current_status|"
    r"(?:timeline|attention|decisions|deadlines|related_processes|attachments):[0-9]+)$"
)
_MATERIAL_LIST_FIELDS = (
    "timeline",
    "attention",
    "decisions",
    "deadlines",
    "related_processes",
    "attachments",
)


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    evidence_ref: str
    kind: str
    step_id: UUID | None = None
    attachment_chunk_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MaterialClaim:
    claim_id: str
    claim_class: str
    text: str
    evidence_refs: tuple[str, ...]


def process_evidence_ref(version_id: UUID) -> str:
    return f"p-{version_id.hex}"


def movement_evidence_ref(step_id: UUID) -> str:
    return f"m-{step_id.hex}"


def attachment_evidence_ref(chunk_id: UUID) -> str:
    return f"a-{chunk_id.hex}"


def _claim_class(claim_id: str) -> str:
    prefix = claim_id.split(":", 1)[0]
    return {
        "synthesis": "synthesis",
        "current_status": "current_status",
        "timeline": "procedural_event",
        "attention": "attention",
        "decisions": "decision",
        "deadlines": "deadline",
        "related_processes": "related_process",
        "attachments": "attachment",
    }[prefix]


def expected_material_claims(payload: dict[str, Any]) -> dict[str, str]:
    expected = {
        "synthesis": str(payload.get("synthesis") or ""),
        "current_status": str(payload.get("current_status") or ""),
    }
    for field in _MATERIAL_LIST_FIELDS:
        items = payload.get(field)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            expected[f"{field}:{index}"] = str(item)
    return expected


def build_material_claims(
    payload: dict[str, Any],
    *,
    evidence_refs: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    refs = [str(ref) for ref in evidence_refs]
    return [
        {
            "claim_id": claim_id,
            "text": text,
            "evidence_refs": list(refs),
        }
        for claim_id, text in expected_material_claims(payload).items()
    ]


def evidence_catalog(context: dict[str, Any]) -> dict[str, EvidenceSource]:
    catalog: dict[str, EvidenceSource] = {}
    process_ref = str(context.get("_process_evidence_ref") or "")
    if _EVIDENCE_REF_RE.fullmatch(process_ref):
        catalog[process_ref] = EvidenceSource(process_ref, "process")

    for source in context.get("_selected_sources", []):
        if not isinstance(source, dict):
            continue
        ref = str(source.get("evidence_ref") or "")
        step_id = source.get("step_id")
        if _EVIDENCE_REF_RE.fullmatch(ref) and isinstance(step_id, UUID):
            catalog[ref] = EvidenceSource(ref, "movement", step_id=step_id)

    for source in context.get("_attachment_sources", []):
        if not isinstance(source, dict):
            continue
        ref = str(source.get("evidence_ref") or "")
        chunk_id = source.get("attachment_chunk_id")
        if _EVIDENCE_REF_RE.fullmatch(ref) and isinstance(chunk_id, UUID):
            catalog[ref] = EvidenceSource(
                ref,
                "attachment",
                attachment_chunk_id=chunk_id,
            )
    return catalog


def validate_claim_evidence(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[list[MaterialClaim], list[str]]:
    expected = expected_material_claims(payload)
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return [], ["structured summary must include claims"]

    catalog = evidence_catalog(context)
    claims: list[MaterialClaim] = []
    errors: list[str] = []
    seen: set[str] = set()

    for raw in raw_claims:
        if not isinstance(raw, dict):
            errors.append("structured summary claim must be an object")
            continue
        claim_id = str(raw.get("claim_id") or "").strip()
        text = str(raw.get("text") or "").strip()
        refs = raw.get("evidence_refs")
        if not _CLAIM_ID_RE.fullmatch(claim_id):
            errors.append(f"invalid claim_id: {claim_id or '<empty>'}")
            continue
        if claim_id in seen:
            errors.append(f"duplicate claim_id: {claim_id}")
            continue
        seen.add(claim_id)
        if claim_id not in expected:
            errors.append(f"unexpected material claim: {claim_id}")
            continue
        if text != expected[claim_id]:
            errors.append(f"claim text does not match structured field: {claim_id}")
        if not isinstance(refs, list) or not refs:
            errors.append(f"material claim lacks evidence_refs: {claim_id}")
            refs = []
        rendered_refs: list[str] = []
        seen_refs: set[str] = set()
        for ref_value in refs:
            ref = str(ref_value or "").strip()
            if ref in seen_refs:
                errors.append(f"duplicate evidence ref for {claim_id}: {ref}")
                continue
            seen_refs.add(ref)
            if not _EVIDENCE_REF_RE.fullmatch(ref):
                errors.append(f"invalid evidence ref for {claim_id}: {ref or '<empty>'}")
                continue
            if ref not in catalog:
                errors.append(f"unknown evidence ref for {claim_id}: {ref}")
                continue
            rendered_refs.append(ref)
        claims.append(
            MaterialClaim(
                claim_id=claim_id,
                claim_class=_claim_class(claim_id),
                text=text,
                evidence_refs=tuple(rendered_refs),
            )
        )

    for claim_id in expected:
        if claim_id not in seen:
            errors.append(f"missing material claim provenance: {claim_id}")

    return claims, errors


def _declared_claim_evidence_refs(
    summary: dict[str, Any],
    expected: dict[str, str],
) -> dict[str, tuple[str, ...]] | None:
    raw_claims = summary.get("claims")
    if not isinstance(raw_claims, list):
        return None

    declared: dict[str, tuple[str, ...]] = {}
    for item in raw_claims:
        if not isinstance(item, dict):
            return None
        claim_id = str(item.get("claim_id") or "").strip()
        text = str(item.get("text") or "").strip()
        refs = item.get("evidence_refs")
        if (
            claim_id not in expected
            or claim_id in declared
            or text != expected[claim_id]
            or not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, str) or not _EVIDENCE_REF_RE.fullmatch(ref)
                for ref in refs
            )
            or len(set(refs)) != len(refs)
        ):
            return None
        declared[claim_id] = tuple(refs)

    if set(declared) != set(expected):
        return None
    return declared


def claim_evidence_is_complete(
    structured_output: dict[str, Any] | None,
    claim_evidence: list[dict[str, Any]],
) -> bool:
    if not structured_summary_document_is_canonical(structured_output):
        return False
    summary = structured_output.get("summary")
    if not isinstance(summary, dict):
        return False

    expected = expected_material_claims(summary)
    if not expected:
        return False
    declared = _declared_claim_evidence_refs(summary, expected)
    if declared is None:
        return False

    observed: dict[str, dict[str, Any]] = {}
    for item in claim_evidence:
        if not isinstance(item, dict):
            return False
        claim_id = str(item.get("claim_id") or "").strip()
        text = str(item.get("text") or "").strip()
        refs = item.get("evidence_refs")
        claim_class = item.get("claim_class")
        if (
            claim_id not in expected
            or claim_id in observed
            or text != expected[claim_id]
            or (
                claim_class is not None
                and str(claim_class) != _claim_class(claim_id)
            )
            or not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, str) or not _EVIDENCE_REF_RE.fullmatch(ref)
                for ref in refs
            )
            or len(set(refs)) != len(refs)
            or tuple(refs) != declared[claim_id]
        ):
            return False
        observed[claim_id] = item

    return set(observed) == set(expected)


def _is_evaluated_status(status: Any) -> bool:
    return status is not None and status != "not_evaluated"


def _valid_verification_reason(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_excerpt_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _claim_verification_metadata(
    item: dict[str, Any],
) -> tuple[bool, bool]:
    status = item.get("verification_status")
    if status is not None and status not in VERIFICATION_STATUSES:
        return False, False
    if status == "contradicted":
        return False, False
    evaluated = _is_evaluated_status(status)
    if evaluated and not _valid_verification_reason(item.get("verification_reason")):
        return False, False
    return True, evaluated


def _source_verification_metadata(
    source: dict[str, Any],
) -> tuple[bool, bool]:
    status = source.get("verification_status")
    if status is not None and status not in VERIFICATION_STATUSES:
        return False, False
    if status == "contradicted":
        return False, False

    digest = source.get("evidence_excerpt_sha256")
    if digest is not None and not _valid_excerpt_sha256(digest):
        return False, False

    evaluated = _is_evaluated_status(status)
    if evaluated:
        if not _valid_verification_reason(source.get("verification_reason")):
            return False, False
        if not _valid_excerpt_sha256(digest):
            return False, False
    return True, evaluated


def claim_evidence_is_publishable(
    structured_output: dict[str, Any] | None,
    claim_evidence: list[dict[str, Any]],
    *,
    process: Any,
) -> bool:
    try:
        context = {
            "code": process["code"],
            "class_name": process["class_name"],
            "court": process["court"],
            "header": decode_json_object(
                process["header"],
                label="summary process header",
            ),
            "parties": decode_json_list(
                process["parties"],
                label="summary process parties",
            ),
            "representatives": decode_json_list(
                process["representatives"]
                if "representatives" in getattr(process, "keys", lambda: [])()
                else [],
                label="summary process representatives",
            ),
        }
    except (KeyError, TypeError, ValueError):
        return False
    if not claim_evidence_is_complete(structured_output, claim_evidence):
        return False
    if not structured_summary_document_matches_process(structured_output, context):
        return False

    for item in claim_evidence:
        valid_claim_metadata, evaluated_claim = _claim_verification_metadata(item)
        if not valid_claim_metadata:
            return False

        sources = item.get("sources")
        if sources is None:
            if evaluated_claim:
                return False
            continue
        if not isinstance(sources, list):
            return False

        evaluated_relation_seen = False
        for source in sources:
            if not isinstance(source, dict):
                return False
            valid_source_metadata, evaluated_source = _source_verification_metadata(
                source
            )
            if not valid_source_metadata:
                return False
            evaluated_relation_seen = evaluated_relation_seen or evaluated_source

        if evaluated_claim and not evaluated_relation_seen:
            return False
    return True


async def replace_summary_claim_evidence(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    claims: list[MaterialClaim],
    catalog: dict[str, EvidenceSource],
    verification: dict[str, ClaimVerification] | None = None,
) -> None:
    await conn.execute(
        "DELETE FROM process_summary_claims WHERE summary_id = $1",
        summary_id,
    )
    if not claims:
        return

    verification = verification or {}
    for claim in claims:
        claim_verification = verification.get(claim.claim_id)
        verification_status = (
            claim_verification.status
            if claim_verification is not None
            else "not_evaluated"
        )
        verification_reason = (
            claim_verification.reason
            if claim_verification is not None
            else "semantic_verifier_not_run"
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text,
                verification_status, verification_reason
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
            claim.claim_id,
            claim.claim_class,
            claim.text,
            verification_status,
            verification_reason,
        )
        if claim_row_id is None:
            raise RuntimeError("claim row was not returned")

        relation_by_ref = (
            {
                relation.evidence_ref: relation
                for relation in claim_verification.relations
            }
            if claim_verification is not None
            else {}
        )
        records: list[tuple[Any, ...]] = []
        excerpt_records: list[tuple[Any, ...]] = []
        for source_order, ref in enumerate(claim.evidence_refs):
            source = catalog.get(ref)
            if source is None:
                raise ValueError(f"claim evidence ref is not in validated catalog: {ref}")
            relation = relation_by_ref.get(ref)
            records.append(
                (
                    claim_row_id,
                    summary_id,
                    process_id,
                    version_id,
                    ref,
                    source.kind,
                    source.step_id,
                    source.attachment_chunk_id,
                    source_order,
                    relation.status if relation is not None else "not_evaluated",
                    (
                        relation.reason
                        if relation is not None
                        else "semantic_verifier_not_run"
                    ),
                    (
                        relation.evidence_excerpt_sha256
                        if relation is not None
                        else None
                    ),
                    relation.page_start if relation is not None else None,
                    relation.page_end if relation is not None else None,
                    relation.char_start if relation is not None else None,
                    relation.char_end if relation is not None else None,
                )
            )
            if relation is not None and relation.evidence_excerpt:
                excerpt_records.append(
                    (claim_row_id, ref, relation.evidence_excerpt)
                )
        await conn.executemany(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, step_id, attachment_chunk_id, source_order,
                verification_status, verification_reason,
                evidence_excerpt_sha256,
                page_start, page_end, char_start, char_end
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13, $14, $15, $16
            )
            """,
            records,
        )
        if excerpt_records:
            await conn.executemany(
                """
                INSERT INTO process_summary_claim_evidence_excerpts (
                    claim_row_id, evidence_ref, evidence_excerpt
                ) VALUES ($1, $2, $3)
                """,
                excerpt_records,
            )


async def load_summary_claim_evidence(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT c.id, c.claim_id, c.claim_class, c.claim_text,
               c.verification_status, c.verification_reason,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'evidence_ref', s.evidence_ref,
                           'source_kind', s.source_kind,
                           'source_order', s.source_order,
                           'verification_status', s.verification_status,
                           'verification_reason', s.verification_reason,
                           'evidence_excerpt_sha256', s.evidence_excerpt_sha256,
                           'page_start', s.page_start,
                           'page_end', s.page_end,
                           'char_start', s.char_start,
                           'char_end', s.char_end
                       )
                       ORDER BY s.source_order
                   ) FILTER (
                       WHERE s.evidence_ref IS NOT NULL
                         AND s.summary_id = c.summary_id
                         AND s.process_id = c.process_id
                         AND s.version_id = c.version_id
                         AND (
                             (
                                 s.source_kind = 'process'
                                 AND s.step_id IS NULL
                                 AND s.attachment_chunk_id IS NULL
                                 AND s.evidence_ref =
                                     'p-' || replace(s.version_id::text, '-', '')
                             )
                             OR (
                                 s.source_kind = 'movement'
                                 AND s.step_id IS NOT NULL
                                 AND s.attachment_chunk_id IS NULL
                                 AND s.evidence_ref =
                                     'm-' || replace(s.step_id::text, '-', '')
                                 AND EXISTS (
                                     SELECT 1
                                     FROM process_summary_sources used
                                     WHERE used.summary_id = s.summary_id
                                       AND used.process_id = s.process_id
                                       AND used.version_id = s.version_id
                                       AND used.step_id = s.step_id
                                 )
                             )
                             OR (
                                 s.source_kind = 'attachment'
                                 AND s.step_id IS NULL
                                 AND s.attachment_chunk_id IS NOT NULL
                                 AND s.evidence_ref =
                                     'a-' || replace(
                                         s.attachment_chunk_id::text, '-', ''
                                     )
                                 AND EXISTS (
                                     SELECT 1
                                     FROM process_summary_attachment_sources used
                                     WHERE used.summary_id = s.summary_id
                                       AND used.process_id = s.process_id
                                       AND used.version_id = s.version_id
                                       AND used.attachment_chunk_id =
                                           s.attachment_chunk_id
                                 )
                             )
                         )
                   ),
                   '[]'::jsonb
               ) AS sources
        FROM process_summary_claims c
        JOIN process_summaries summary
          ON summary.id = c.summary_id
         AND summary.process_id = c.process_id
         AND summary.version_id = c.version_id
        JOIN process_versions version
          ON version.id = c.version_id
         AND version.process_id = c.process_id
        LEFT JOIN process_summary_claim_sources s ON s.claim_row_id = c.id
        WHERE c.summary_id = $1
        GROUP BY c.id, c.claim_id, c.claim_class, c.claim_text,
                 c.verification_status, c.verification_reason
        ORDER BY c.claim_id
        """,
        summary_id,
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        raw_sources = decode_json_list(
            row["sources"],
            label="claim evidence sources",
        )
        sources = [
            {
                "evidence_ref": str(item["evidence_ref"]),
                "source_kind": str(item["source_kind"]),
                "source_order": int(item["source_order"]),
                "verification_status": str(item["verification_status"]),
                "verification_reason": (
                    str(item["verification_reason"])
                    if item.get("verification_reason") is not None
                    else None
                ),
                "evidence_excerpt_sha256": (
                    str(item["evidence_excerpt_sha256"])
                    if item.get("evidence_excerpt_sha256") is not None
                    else None
                ),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "char_start": item.get("char_start"),
                "char_end": item.get("char_end"),
            }
            for item in raw_sources
            if isinstance(item, dict) and item.get("evidence_ref") is not None
        ]
        result.append(
            {
                "claim_id": str(row["claim_id"]),
                "claim_class": str(row["claim_class"]),
                "text": str(row["claim_text"]),
                "evidence_refs": [source["evidence_ref"] for source in sources],
                "verification_status": str(row["verification_status"]),
                "verification_reason": (
                    str(row["verification_reason"])
                    if row["verification_reason"] is not None
                    else None
                ),
                "sources": sources,
            }
        )
    return result

