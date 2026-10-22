"""Verifica o banco isolado e cria apenas seu tenant vazio, de forma idempotente."""
import asyncio
import json
import os
from uuid import UUID

import asyncpg

MIGRATION = "034_acquisition_authorizations.sql"
HOMOLOGATION_TEST_TOKEN = "ragv-hml-local-test-2026"


def tenant_from_environment():
    """Falha antes de conectar se a configuração sintética estiver incompleta."""
    mapping = json.loads(os.environ["RPY_BEARER_TOKENS"])
    if not isinstance(mapping, dict) or len(mapping) != 1:
        raise ValueError("tenant_mapping")
    token, tenant = next(iter(mapping.items()))
    if not isinstance(token, str) or (
        len(token) < 32
        and not (
            token == HOMOLOGATION_TEST_TOKEN
            and os.environ.get("RPY_HOMOLOGATION_MODE", "").lower() == "true"
            and os.environ.get("RPY_HOMOLOGATION_BIND_ADDRESS") == "127.0.0.1"
        )
    ):
        raise ValueError("tenant_token")
    return UUID(tenant)


async def prepare_tenant(tenant):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)
    try:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE filename=$1)", MIGRATION
            ):
                return {"status": "migration_missing"}, 4
            if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')"):
                return {"status": "pgvector_missing"}, 4
            await conn.execute(
                "INSERT INTO tenants(id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
                tenant, "Homologação local - Grupo Villela",
            )
            exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)", tenant)
            if not exists:
                return {"status": "tenant_missing"}, 5
        return {"status": "ready", "tenant_exists": True, "migration": MIGRATION}, 0
    finally:
        await conn.close()


def main():
    try:
        tenant = tenant_from_environment()
    except (KeyError, ValueError, TypeError):
        result, code = {"status": "invalid_tenant_configuration"}, 2
    else:
        try:
            result, code = asyncio.run(prepare_tenant(tenant))
        except (asyncpg.PostgresError, OSError, TimeoutError, KeyError, ValueError):
            result, code = {"status": "database_unavailable"}, 3
    # Saída fechada: nunca imprime token, UUID, DSN ou mensagem/traceback de driver.
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
