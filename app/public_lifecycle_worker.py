from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.claim_evidence import claim_evidence_is_publishable, load_summary_claim_evidence
from app.json_utils import decode_json_object
from app.public_lifecycle import (
    transition_job_public_requests,
    transition_requests_for_judit_request,
    transition_requests_for_version,
)
from app.summary_policy import is_restricted_local_summary


async def mark_job_started(
    conn: asyncpg.Connection,
    *,
    task_name: str,
    payload: dict[str, Any],
) -> None:
    if task_name == "generate_process_summary":
        await transition_job_public_requests(
            conn,
            task_name=task_name,
            payload=payload,
            status="generating",
        )


async def reconcile_generation_result(
    conn: asyncpg.Connection,
    *,
    payload: dict[str, Any],
    result: dict[str, Any] | None,
) -> None:
    """Map a completed generation handler to validating + public terminal state."""
    try:
        process_id = UUID(str(payload["process_id"]))
        version_id = UUID(str(payload["version_id"]))
    except (KeyError, TypeError, ValueError):
        return

    await transition_job_public_requests(
        conn,
        task_name="generate_process_summary",
        payload=payload,
        status="validating",
    )

    row = await conn.fetchrow(
        """
        SELECT ps.id AS summary_id,
               ps.structured_output,
               ps.model,
               ps.prompt_version,
               COALESCE((ps.validation->>'passed')::boolean, false) AS passed,
               p.secrecy_level,
               p.code,
               p.class_name,
               p.court,
               p.header,
               p.parties,
               p.representatives,
               p.updated_at AS source_updated_at
        FROM process_summaries ps
        JOIN processes p
          ON p.id = ps.process_id
         AND p.current_version_id = ps.version_id
        WHERE ps.process_id = $1 AND ps.version_id = $2
        """,
        process_id,
        version_id,
    )
    result_validation = (result or {}).get("validation")
    result_passed = bool(
        isinstance(result_validation, dict) and result_validation.get("passed") is True
    )
    passed = False
    if row is not None and row["passed"] and result_passed:
        if int(row["secrecy_level"] or 0) > 0:
            passed = is_restricted_local_summary(row, process=row)
        else:
            claim_evidence = await load_summary_claim_evidence(
                conn, summary_id=row["summary_id"]
            )
            structured_output = (
                decode_json_object(
                    row["structured_output"],
                    label="generated summary structured output",
                )
                if row["structured_output"] is not None
                else None
            )
            passed = claim_evidence_is_publishable(
                structured_output,
                claim_evidence,
                process=row,
            )

    status = "completed" if passed else "validation_failed"
    error_code = None if passed else "validation_failed"
    summary_id = row["summary_id"] if row is not None and passed else None
    source_updated_at = row["source_updated_at"] if row is not None else None
    flags = {
        "summary_reused": bool((result or {}).get("reused")),
        "summary_persisted": bool((result or {}).get("persisted")),
    }

    judit_request_id = payload.get("judit_request_id")
    if judit_request_id:
        await transition_requests_for_judit_request(
            conn,
            judit_request_id=str(judit_request_id),
            status=status,
            process_id=process_id,
            version_id=version_id,
            summary_id=summary_id,
            source_updated_at=source_updated_at,
            flags=flags,
            error_code=error_code,
        )
    else:
        await transition_requests_for_version(
            conn,
            version_id=version_id,
            status=status,
            process_id=process_id,
            summary_id=summary_id,
            source_updated_at=source_updated_at,
            flags=flags,
            error_code=error_code,
        )


async def mark_job_dead(
    conn: asyncpg.Connection,
    *,
    task_name: str,
    payload: dict[str, Any],
    error_code: str = "job_failed",
) -> None:
    await transition_job_public_requests(
        conn,
        task_name=task_name,
        payload=payload,
        status="failed",
        error_code=error_code,
    )
