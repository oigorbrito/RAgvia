from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.migrations import migrate
from app.related_processes import (
    load_related_process_context,
    safe_related_provenance,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

ORIGIN_CODE = "0000000-00.2026.8.21.1120"
RELATED_A_CODE = "0000000-00.2026.8.21.1121"
RELATED_B_CODE = "0000000-00.2026.8.21.1122"
SECRET_CODE = "0000000-00.2026.8.21.1123"


@pytest.mark.asyncio
async def test_related_process_lookup_is_tenant_scoped_and_provenance_is_safe() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    tenant_a = uuid4()
    tenant_b = uuid4()
    origin_id = uuid4()
    related_a_id = uuid4()
    related_b_id = uuid4()
    secret_id = uuid4()
    origin_version = uuid4()
    related_a_version = uuid4()
    related_b_version = uuid4()
    secret_version = uuid4()
    related_a_public_step = uuid4()
    related_a_private_step = uuid4()
    related_b_step = uuid4()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            await conn.executemany(
                "INSERT INTO tenants (id, name) VALUES ($1, $2)",
                [(tenant_a, "related tenant A"), (tenant_b, "related tenant B")],
            )
            await conn.executemany(
                """
                INSERT INTO processes (
                    id, code, court, class_name, secrecy_level, header
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                [
                    (origin_id, ORIGIN_CODE, "TJRS", "Origem", 0, '{"instance":1}'),
                    (related_a_id, RELATED_A_CODE, "TJRS", "Relacionada A", 0, '{"instance":1}'),
                    (related_b_id, RELATED_B_CODE, "TJRS", "Relacionada B", 0, '{"instance":1}'),
                    (secret_id, SECRET_CODE, "TJRS", "Sigilosa", 1, '{"instance":1}'),
                ],
            )
            await conn.executemany(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, $3, TRUE)
                """,
                [
                    (origin_version, origin_id, "related-origin"),
                    (related_a_version, related_a_id, "related-a"),
                    (related_b_version, related_b_id, "related-b"),
                    (secret_version, secret_id, "related-secret"),
                ],
            )
            await conn.executemany(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                [
                    (origin_id, origin_version),
                    (related_a_id, related_a_version),
                    (related_b_id, related_b_version),
                    (secret_id, secret_version),
                ],
            )
            # Both tenants may read the origin, but each owns a different related process.
            await conn.executemany(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
                [
                    (tenant_a, origin_id),
                    (tenant_b, origin_id),
                    (tenant_a, related_a_id),
                    (tenant_b, related_b_id),
                    (tenant_a, secret_id),
                ],
            )
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    id, version_id, process_id, step_number, title, text, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                [
                    (
                        related_a_public_step,
                        related_a_version,
                        related_a_id,
                        1,
                        "DECISÃO",
                        "Movimento público relacionado do tenant A.",
                        '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}'
                        % RELATED_A_CODE,
                    ),
                    (
                        related_a_private_step,
                        related_a_version,
                        related_a_id,
                        2,
                        "PETIÇÃO",
                        "Conteúdo privado que não pode entrar no contexto.",
                        '{"cnj":"%s","instance":1,"private":true,"secrecy_level":0}'
                        % RELATED_A_CODE,
                    ),
                    (
                        related_b_step,
                        related_b_version,
                        related_b_id,
                        1,
                        "DECISÃO",
                        "Movimento público relacionado do tenant B.",
                        '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}'
                        % RELATED_B_CODE,
                    ),
                ],
            )

            candidates = [
                RELATED_B_CODE,
                RELATED_A_CODE,
                SECRET_CODE,
                ORIGIN_CODE,
                RELATED_A_CODE,
            ]
            context_a = await load_related_process_context(
                conn,
                tenant_id=tenant_a,
                origin_process_id=origin_id,
                related_codes=candidates,
            )
            context_b = await load_related_process_context(
                conn,
                tenant_id=tenant_b,
                origin_process_id=origin_id,
                related_codes=candidates,
            )

        assert [item.code for item in context_a] == [RELATED_A_CODE]
        assert [item.code for item in context_b] == [RELATED_B_CODE]
        assert [step.id for step in context_a[0].movements] == [related_a_public_step]
        assert [step.id for step in context_b[0].movements] == [related_b_step]
        assert related_a_private_step not in {step.id for step in context_a[0].movements}

        provenance = safe_related_provenance(context_a)
        assert provenance == [
            {
                "kind": "related_process_movement",
                "process_code": RELATED_A_CODE,
                "step_id": str(related_a_public_step),
                "step_number": 1,
                "occurred_at": None,
                "source_order": 0,
            }
        ]
        serialized = repr(provenance)
        assert "Movimento público" not in serialized
        assert "Conteúdo privado" not in serialized
        assert "source_payload" not in serialized
        assert str(related_b_id) not in serialized
        assert str(secret_id) not in serialized
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_related_process_lookup_requires_origin_authorization() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    tenant = uuid4()
    unauthorized_tenant = uuid4()
    origin_id = uuid4()
    related_id = uuid4()
    origin_version = uuid4()
    related_version = uuid4()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            await conn.executemany(
                "INSERT INTO tenants (id, name) VALUES ($1, $2)",
                [(tenant, "authorized"), (unauthorized_tenant, "unauthorized")],
            )
            await conn.executemany(
                "INSERT INTO processes (id, code) VALUES ($1, $2)",
                [(origin_id, ORIGIN_CODE), (related_id, RELATED_A_CODE)],
            )
            await conn.executemany(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, $3, TRUE)
                """,
                [
                    (origin_version, origin_id, "origin-authorization"),
                    (related_version, related_id, "related-authorization"),
                ],
            )
            await conn.executemany(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                [(origin_id, origin_version), (related_id, related_version)],
            )
            await conn.executemany(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
                [(tenant, origin_id), (unauthorized_tenant, related_id)],
            )

            result = await load_related_process_context(
                conn,
                tenant_id=unauthorized_tenant,
                origin_process_id=origin_id,
                related_codes=[RELATED_A_CODE],
            )

        assert result == []
    finally:
        await pool.close()
