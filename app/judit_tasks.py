from __future__ import annotations

import os
from typing import Any

from app.claim_evidence import claim_evidence_is_publishable, load_summary_claim_evidence
from app.datajud_client import lookup_datajud_metadata
from app.datajud_enrichment import merge_datajud_metadata
from app.datajud_provenance import replace_datajud_field_provenance
from app.db import create_pool
from app.json_utils import decode_json_object
from app.judit import extract_promotable_fields, parse_event
from app.judit_client import judit_attachments_enabled
from app.processes import finalize_version, preferred_judit_version
from app.public_lifecycle import transition_requests_for_judit_request
from app.queue import enqueue
from app.summary_policy import is_restricted_local_summary
from app.tasks import task


async def _complete_from_current_summary(
    conn,
    *,
    request_id: str,
    process_id,
) -> bool:
    current = await conn.fetchrow(
        """
        SELECT p.current_version_id AS version_id,
               p.secrecy_level,
               p.code,
               p.class_name,
               p.court,
               p.header,
               p.parties,
               p.representatives,
               ps.id AS summary_id,
               ps.structured_output,
               ps.model,
               ps.prompt_version,
               p.updated_at AS source_updated_at
        FROM processes p
        LEFT JOIN process_summaries ps
          ON ps.process_id = p.id
         AND ps.version_id = p.current_version_id
         AND COALESCE((ps.validation->>'passed')::boolean, false) = true
        WHERE p.id = $1
        """,
        process_id,
    )
    if current is None or current["version_id"] is None or current["summary_id"] is None:
        return False

    if int(current["secrecy_level"] or 0) > 0:
        if not is_restricted_local_summary(current, process=current):
            return False
    else:
        claim_evidence = await load_summary_claim_evidence(
            conn, summary_id=current["summary_id"]
        )
        structured_output = (
            decode_json_object(
                current["structured_output"],
                label="current summary structured output",
            )
            if current["structured_output"] is not None
            else None
        )
        if not claim_evidence_is_publishable(
            structured_output,
            claim_evidence,
            process=current,
        ):
            return False

    await transition_requests_for_judit_request(
        conn,
        judit_request_id=request_id,
        status="completed",
        process_id=process_id,
        version_id=current["version_id"],
        summary_id=current["summary_id"],
        source_updated_at=current["source_updated_at"],
        flags={"reused_existing_summary": True},
    )
    return True


@task("finalize_judit_request")
async def finalize_judit_request_task(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            # DataJud is supplementary and may require network I/O. Prepare it before
            # the promotion transaction, then revalidate that the preferred Judit
            # version is still the same before applying the result.
            prepared_version_id = None
            prepared_fields: dict[str, Any] | None = None
            prepared_datajud_result = None
            datajud_status = "not_attempted"

            pre_staged = await preferred_judit_version(conn, request_id=request_id)
            if pre_staged is not None:
                pre_source_payload = decode_json_object(
                    pre_staged["source_payload"],
                    label="staged Judit source payload",
                )
                pre_event = parse_event(pre_source_payload)
                if pre_event.response_data:
                    prepared_version_id = pre_staged["version_id"]
                    judit_fields = extract_promotable_fields(pre_event.response_data)
                    prepared_fields = judit_fields
                    if bool(pre_staged["finalized"]):
                        datajud_status = "already_finalized"
                    else:
                        lookup = await lookup_datajud_metadata(
                            code=str(pre_staged["code"]),
                            secrecy_level=int(judit_fields.get("secrecy_level") or 0),
                        )
                        datajud_status = lookup.status
                        if lookup.status == "ok" and lookup.metadata is not None:
                            prepared_datajud_result = merge_datajud_metadata(
                                judit_fields,
                                lookup.metadata,
                            )
                            prepared_fields = prepared_datajud_result.process

            # Promotion, DataJud provenance and summary enqueue are one durable unit.
            # finalize_version()/provenance replacement use nested savepoints, so an
            # enqueue failure rolls the entire promotion back and lets the finalizer
            # job retry safely.
            async with conn.transaction():
                await transition_requests_for_judit_request(
                    conn,
                    judit_request_id=request_id,
                    status="indexing",
                )
                staged = await preferred_judit_version(conn, request_id=request_id)
                if staged is None:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="source_unavailable",
                        error_code="no_lawsuit_response",
                    )
                    return {
                        "request_id": request_id,
                        "status": "no_lawsuit_response",
                        "datajud_status": datajud_status,
                    }

                source_payload = decode_json_object(
                    staged["source_payload"], label="staged Judit source payload"
                )
                staged_event = parse_event(source_payload)
                if not staged_event.response_data:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="source_unavailable",
                        error_code="missing_response_data",
                    )
                    return {
                        "request_id": request_id,
                        "status": "missing_response_data",
                        "datajud_status": datajud_status,
                    }

                fresh_fields = extract_promotable_fields(staged_event.response_data)
                datajud_result = None
                if (
                    prepared_version_id == staged["version_id"]
                    and prepared_fields is not None
                ):
                    fields = prepared_fields
                    datajud_result = prepared_datajud_result
                else:
                    fields = fresh_fields
                    if (
                        prepared_version_id is not None
                        and prepared_version_id != staged["version_id"]
                    ):
                        datajud_status = "staged_changed"

                promoted = await finalize_version(
                    conn,
                    process_id=staged["process_id"],
                    version_id=staged["version_id"],
                    **fields,
                )
                if datajud_result is not None:
                    await replace_datajud_field_provenance(
                        conn,
                        process_id=staged["process_id"],
                        version_id=staged["version_id"],
                        result=datajud_result,
                    )

                equivalent_to_version_id = None
                if not promoted:
                    equivalent_to_version_id = await conn.fetchval(
                        """
                        SELECT equivalent_to_version_id
                        FROM process_versions
                        WHERE id = $1 AND process_id = $2
                        """,
                        staged["version_id"],
                        staged["process_id"],
                    )

                summary_enqueued = False
                attachment_processing_enqueued = False
                if promoted:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="indexing",
                        process_id=staged["process_id"],
                        version_id=staged["version_id"],
                    )

                if promoted and not bool(staged["source_cached_response"]):
                    attachment_manifest = fields.get("attachments") or []
                    if attachment_manifest and judit_attachments_enabled():
                        job = await enqueue(
                            conn,
                            task_name="process_judit_attachments",
                            payload={
                                "process_id": str(staged["process_id"]),
                                "version_id": str(staged["version_id"]),
                                "judit_request_id": request_id,
                            },
                            idempotency_key=f"attachments:{staged['version_id']}",
                        )
                        attachment_processing_enqueued = job is not None
                    else:
                        job = await enqueue(
                            conn,
                            task_name="generate_process_summary",
                            payload={
                                "process_id": str(staged["process_id"]),
                                "version_id": str(staged["version_id"]),
                                "code": staged["code"],
                                "judit_request_id": request_id,
                            },
                            idempotency_key=f"summary:{staged['version_id']}",
                        )
                        summary_enqueued = job is not None
                elif await _complete_from_current_summary(
                    conn,
                    request_id=request_id,
                    process_id=staged["process_id"],
                ):
                    pass
                elif not promoted and equivalent_to_version_id is not None:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="indexing",
                        process_id=staged["process_id"],
                        version_id=equivalent_to_version_id,
                    )
                    job = await enqueue(
                        conn,
                        task_name="generate_process_summary",
                        payload={
                            "process_id": str(staged["process_id"]),
                            "version_id": str(equivalent_to_version_id),
                            "code": staged["code"],
                            "judit_request_id": request_id,
                        },
                        idempotency_key=(
                            f"summary-repair:{equivalent_to_version_id}:{request_id}"
                        ),
                    )
                    summary_enqueued = job is not None
                else:
                    error_code = (
                        "cached_response_not_generated"
                        if promoted and bool(staged["source_cached_response"])
                        else "current_summary_unavailable"
                    )
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="failed",
                        process_id=staged["process_id"],
                        version_id=(
                            staged["version_id"]
                            if promoted
                            else equivalent_to_version_id
                        ),
                        error_code=error_code,
                    )

        if promoted:
            status = "finalized"
        elif equivalent_to_version_id is not None:
            status = "finalized_unchanged"
        else:
            status = "finalized_stale"
        return {
            "request_id": request_id,
            "status": status,
            "promoted": promoted,
            "cached_response": bool(staged["source_cached_response"]),
            "summary_enqueued": summary_enqueued,
            "attachment_processing_enqueued": attachment_processing_enqueued,
            "equivalent_to_version_id": (
                str(equivalent_to_version_id)
                if equivalent_to_version_id is not None
                else None
            ),
            "datajud_status": datajud_status,
        }
    finally:
        await pool.close()

