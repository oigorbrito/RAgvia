from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from app.auth import configured_bearer_tokens
from app.claim_evidence import (
    build_material_claims,
    claim_evidence_is_publishable,
    evidence_catalog,
    load_summary_claim_evidence,
    validate_claim_evidence,
)
from app.db import create_pool
from app.json_utils import decode_json_object
from app.processes import finalize_version, stage_version
from app.rag import _load_context, _persist_summary, _validate_provider_summary
from app.summary_output import structured_summary_document

DEMO_TOKEN = os.getenv("RPY_DEMO_BEARER_TOKEN", "dev-local-token")
DEMO_CODE = "0000000-00.2026.8.21.0001"
DEMO_SOURCE_ID = "local-zero-cost-demo-v1"


async def seed_demo(database_url: str) -> None:
    tokens = configured_bearer_tokens()
    tenant_id = tokens.get(DEMO_TOKEN)
    if tenant_id is None:
        raise RuntimeError(
            f"demo token {DEMO_TOKEN!r} is not present in RPY_BEARER_TOKENS"
        )

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenants (id, name)
                VALUES ($1, 'Local zero-cost demo')
                ON CONFLICT (id) DO NOTHING
                """,
                tenant_id,
            )

            existing = await conn.fetchrow(
                """
                SELECT p.id, p.code, p.class_name, p.court, p.header, p.parties,
                       p.representatives, p.current_version_id
                FROM processes p
                JOIN tenant_processes tp ON tp.process_id = p.id
                WHERE tp.tenant_id = $1 AND p.code = $2
                """,
                tenant_id,
                DEMO_CODE,
            )
            if existing is not None and existing["current_version_id"] is not None:
                summary_row = await conn.fetchrow(
                    """
                    SELECT id, structured_output
                    FROM process_summaries
                    WHERE process_id = $1
                      AND version_id = $2
                      AND COALESCE((validation->>'passed')::boolean, false)
                    """,
                    existing["id"],
                    existing["current_version_id"],
                )
                summary_ok = False
                if summary_row is not None:
                    claim_evidence = await load_summary_claim_evidence(
                        conn, summary_id=summary_row["id"]
                    )
                    structured_output = decode_json_object(
                        summary_row["structured_output"],
                        label="demo structured summary",
                    )
                    summary_ok = claim_evidence_is_publishable(
                        structured_output,
                        claim_evidence,
                        process=existing,
                    )
                if summary_ok:
                    print("RPY LOCAL DEMO: READY")
                    print(f"process={DEMO_CODE}")
                    print(f"token={DEMO_TOKEN}")
                    print("providers=0")
                    return

            payload = {
                "source": "local-zero-cost-demo",
                "response_type": "lawsuit",
                "response_data": {
                    "code": DEMO_CODE,
                    "class_name": "Ação Cível",
                    "court": "TJRS",
                },
            }
            process_id, version_id = await stage_version(
                conn,
                code=DEMO_CODE,
                source_request_id=DEMO_SOURCE_ID,
                cached_response=False,
                payload=payload,
                judit_request_id=None,
                judit_response_id=None,
                judit_callback_id=None,
                tenant_id=tenant_id,
            )

            steps = [
                {
                    "step_number": 1,
                    "occurred_at": datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
                    "title": "Distribuição",
                    "text": "Processo sintético distribuído para fins de demonstração local.",
                    "metadata": {},
                },
                {
                    "step_number": 2,
                    "occurred_at": datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc),
                    "title": "Citação",
                    "text": "Citação sintética registrada no ambiente de demonstração.",
                    "metadata": {},
                },
                {
                    "step_number": 3,
                    "occurred_at": datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
                    "title": "Contestação",
                    "text": "Contestação sintética apresentada para teste do fluxo do produto.",
                    "metadata": {},
                },
            ]
            await finalize_version(
                conn,
                process_id=process_id,
                version_id=version_id,
                header={
                    "instance": 1,
                    "state": "RS",
                    "county": "Porto Alegre",
                    "demo": True,
                },
                parties=[
                    {"name": "Empresa Alfa Ltda.", "side": "active"},
                    {"name": "Empresa Beta Ltda.", "side": "passive"},
                ],
                subjects=[{"name": "Contrato"}],
                steps=steps,
                attachments=[],
                court="TJRS",
                class_name="Ação Cível",
                secrecy_level=0,
            )

        context = await _load_context(
            pool, process_id, version_id, tenant_id=tenant_id
        )
        summary = (
            f"# Resumo do processo\n\n"
            f"Processo {DEMO_CODE}. Situação processual registrada.\n\n"
            "## Pontos de atenção\n"
            "Nenhuma divergência objetiva identificada."
        )
        demo_payload = {
            "synthesis": f"Processo {DEMO_CODE}. Situação processual registrada.",
            "timeline": [],
            "current_status": "Situação processual registrada.",
            "attention": ["Nenhuma divergência objetiva identificada."],
            "decisions": [],
            "deadlines": [],
            "related_processes": [],
            "attachments": [],
        }
        demo_payload["claims"] = build_material_claims(
            demo_payload,
            evidence_refs=[context["_process_evidence_ref"]],
        )
        context["_parsed_summary"] = demo_payload
        structured_output = structured_summary_document(demo_payload, context)
        result = _validate_provider_summary(summary, context)
        if not result.passed:
            raise RuntimeError(
                "synthetic demo summary failed validation: "
                + "; ".join(result.errors)
            )

        claims, claim_errors = validate_claim_evidence(demo_payload, context)
        if claim_errors:
            raise RuntimeError("synthetic demo claim evidence failed validation")

        async with pool.acquire() as conn:
            await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text=summary,
                validation={"passed": True, "errors": []},
                generation_ms=0,
                model="local-demo-no-provider",
                prompt_version="local-demo-v1",
                usage={},
                cache_hit=False,
                cost_usd=0.0,
                selected_sources=list(context.get("_selected_sources", [])),
                attachment_sources=[],
                glossary_sources=list(context.get("_glossary_sources", [])),
                unicode_security_flags=list(
                    context.get("_unicode_security_flags", [])
                ),
                structured_output=structured_output,
                claims=claims,
                evidence_sources=evidence_catalog(context),
            )

        print("RPY LOCAL DEMO: READY")
        print(f"process={DEMO_CODE}")
        print(f"token={DEMO_TOKEN}")
        print("providers=0")
    finally:
        await pool.close()

