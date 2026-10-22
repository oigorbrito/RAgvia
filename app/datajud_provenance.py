from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from app.datajud_enrichment import DataJudMergeResult, FieldProvenance
from app.json_utils import decode_json_value


def datajud_conflict_warning(field: str) -> str:
    return (
        f"Conflito de metadados entre Judit e DataJud no campo {field}; "
        "o valor oficial do DataJud foi usado no contexto normalizado."
    )


async def replace_datajud_field_provenance(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    result: DataJudMergeResult,
) -> None:
    """Replace per-version DataJud decisions atomically.

    Provenance stores only modeled metadata selections. It never stores parties,
    movement text, raw Judit/DataJud payloads, credentials or provider responses.
    """
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM process_datajud_field_provenance WHERE version_id = $1",
            version_id,
        )
        if not result.provenance:
            return
        await conn.executemany(
            """
            INSERT INTO process_datajud_field_provenance (
                process_id,
                version_id,
                field_name,
                selected_source,
                selected_value,
                conflict,
                source_ref
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            """,
            [
                (
                    process_id,
                    version_id,
                    item.field,
                    item.selected_source,
                    json.dumps(item.selected_value, ensure_ascii=False, default=str),
                    item.conflict,
                    item.source_ref,
                )
                for item in result.provenance
            ],
        )


async def load_datajud_field_provenance(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT field_name, selected_source, selected_value, conflict, source_ref
        FROM process_datajud_field_provenance
        WHERE process_id = $1 AND version_id = $2
        ORDER BY field_name
        """,
        process_id,
        version_id,
    )
    return [
        {
            "field": str(row["field_name"]),
            "selected_source": str(row["selected_source"]),
            "selected_value": decode_json_value(row["selected_value"]),
            "conflict": bool(row["conflict"]),
            "source_ref": row["source_ref"],
        }
        for row in rows
    ]


async def load_datajud_conflict_warnings(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT field_name
        FROM process_datajud_field_provenance
        WHERE process_id = $1
          AND version_id = $2
          AND conflict = TRUE
          AND selected_source = 'datajud'
        ORDER BY field_name
        """,
        process_id,
        version_id,
    )
    return [datajud_conflict_warning(str(row["field_name"])) for row in rows]


def provenance_from_dicts(items: list[dict[str, Any]]) -> tuple[FieldProvenance, ...]:
    """Build typed provenance from safe persisted rows for deterministic tests/callers."""
    return tuple(
        FieldProvenance(
            field=str(item["field"]),
            selected_source=str(item["selected_source"]),  # type: ignore[arg-type]
            selected_value=item.get("selected_value"),
            conflict=bool(item.get("conflict")),
            source_ref=(
                str(item["source_ref"]).strip()
                if item.get("source_ref") is not None
                else None
            ),
        )
        for item in items
    )
