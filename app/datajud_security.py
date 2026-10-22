from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.json_utils import decode_json_object
from app.judit import parse_event


class DataJudProcessNotAuthorized(Exception):
    """The tenant has no durable authorization for this process."""


class DataJudProcessNotPublic(Exception):
    """The process is known to be restricted or lacks verified public evidence."""


class DataJudProcessKnownSecret(Exception):
    """A trusted source or persisted classification identifies secrecy."""


async def classify_datajud_lookup(
    conn: asyncpg.Connection, *, tenant_id: UUID, code: str
) -> tuple[str, int | None]:
    """Return verified_public/0, unknown/None, or reject known-secret cases.

    Unknown is not asserted to be public. It is permitted only for this explicit
    metadata-only DataJud operation under the deployment's opt-in authorization.
    """
    row = await conn.fetchrow(
        """SELECT p.id, p.secrecy_level, p.current_version_id,
                  pv.finalized, pv.source_payload,
                  EXISTS (SELECT 1 FROM tenant_processes tp
                          WHERE tp.process_id=p.id AND tp.tenant_id=$1) AS tenant_linked
           FROM processes p
           LEFT JOIN process_versions pv
             ON pv.id=p.current_version_id AND pv.process_id=p.id
           WHERE p.code=$2""",
        tenant_id, code,
    )
    if row is None:
        return "unknown", None
    try:
        stored_level = int(row["secrecy_level"])
    except (TypeError, ValueError):
        stored_level = -1
    if stored_level > 0:
        raise DataJudProcessKnownSecret
    historical_payloads = await conn.fetch(
        "SELECT source_payload FROM process_versions WHERE process_id=$1 AND finalized=TRUE",
        row["id"],
    )
    for historical in historical_payloads:
        try:
            event = parse_event(decode_json_object(historical["source_payload"], label="DataJud source classification"))
            if event.is_lawsuit_response and event.code == code and event.response_data:
                if _explicit_source_secrecy(event.response_data) > 0:
                    raise DataJudProcessKnownSecret
        except DataJudProcessKnownSecret:
            raise
        except (DataJudProcessNotPublic, KeyError, TypeError, ValueError):
            continue
    if row["finalized"] is True:
        try:
            event = parse_event(decode_json_object(row["source_payload"], label="DataJud source classification"))
            if event.is_lawsuit_response and event.code == code and event.response_data:
                source_level = _explicit_source_secrecy(event.response_data)
                if source_level > 0:
                    raise DataJudProcessKnownSecret
                if stored_level == 0 and row["tenant_linked"]:
                    try:
                        await require_public_datajud_process(conn, tenant_id=tenant_id, code=code)
                        return "verified_public", 0
                    except (DataJudProcessNotAuthorized, DataJudProcessNotPublic):
                        pass
        except DataJudProcessKnownSecret:
            raise
        except (DataJudProcessNotPublic, KeyError, TypeError, ValueError):
            pass
    return "unknown", None


def _explicit_source_secrecy(response_data: dict[str, Any]) -> int:
    keys = ("secrecy_level", "secrecyLevel")
    present = [response_data[key] for key in keys if key in response_data]
    if not present:
        raise DataJudProcessNotPublic

    levels: list[int] = []
    for value in present:
        if isinstance(value, bool):
            raise DataJudProcessNotPublic
        if isinstance(value, int):
            level = value
        elif isinstance(value, str) and value.isdigit():
            level = int(value)
        else:
            raise DataJudProcessNotPublic
        if level < 0:
            raise DataJudProcessNotPublic
        levels.append(level)

    if len(set(levels)) != 1:
        raise DataJudProcessNotPublic
    return levels[0]


async def require_public_datajud_process(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
) -> int:
    """Return secrecy level only from an authorized, finalized source record.

    Level zero is trusted only when the immutable current Judit response explicitly
    says zero and agrees with the persisted process classification. Missing or
    inconsistent evidence is fail-closed; the DataJud API's own public filtering is
    not treated as RagV classification evidence.
    """
    row = await conn.fetchrow(
        """
        SELECT p.id, p.secrecy_level, p.current_version_id,
               pv.finalized, pv.source_payload
        FROM processes p
        JOIN tenant_processes tp
          ON tp.process_id = p.id AND tp.tenant_id = $1
        LEFT JOIN process_versions pv
          ON pv.id = p.current_version_id AND pv.process_id = p.id
        WHERE p.code = $2
        """,
        tenant_id,
        code,
    )
    if row is None:
        raise DataJudProcessNotAuthorized
    if row["current_version_id"] is None or row["finalized"] is not True:
        raise DataJudProcessNotPublic

    try:
        source_payload = decode_json_object(
            row["source_payload"], label="DataJud source classification"
        )
        event = parse_event(source_payload)
        if not event.is_lawsuit_response or event.code != code or not event.response_data:
            raise DataJudProcessNotPublic
        source_level = _explicit_source_secrecy(event.response_data)
    except DataJudProcessNotPublic:
        raise
    except (KeyError, TypeError, ValueError):
        raise DataJudProcessNotPublic from None

    try:
        stored_level = int(row["secrecy_level"])
    except (TypeError, ValueError):
        raise DataJudProcessNotPublic from None
    if stored_level != source_level:
        raise DataJudProcessNotPublic
    if source_level > 0:
        raise DataJudProcessNotPublic
    return source_level
