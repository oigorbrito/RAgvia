from __future__ import annotations

import asyncio
import os
from urllib.parse import unquote, urlparse

import asyncpg


RUNTIME_ROLES = (
    ("rpy_api", "API_DATABASE_URL"),
    ("rpy_worker", "WORKER_DATABASE_URL"),
    ("rpy_scheduler", "SCHEDULER_DATABASE_URL"),
    ("rpy_backup", "BACKUP_DATABASE_URL"),
)

API_READ_TABLES = (
    "processes",
    "process_versions",
    "process_summaries",
    "process_summary_sources",
    "process_summary_attachment_sources",
    "process_summary_claims",
    "process_summary_claim_sources",
    "process_attachment_status_counts",
    "process_summary_glossary_sources",
    "tenant_processes",
    "tenant_judit_requests",
    "public_summary_requests",
    "judit_trackings",
    "judit_tracking_refreshes",
    "jobs",
    "judit_deliveries",
    "judit_request_completions",
    "backup_runs",
    "acquisition_authorizations",
    "tenant_judit_request_history",
)


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _credentials(env_name: str, expected_user: str) -> tuple[str, str]:
    raw = os.environ.get(env_name)
    if not raw:
        raise RuntimeError(f"{env_name} is required")
    parsed = urlparse(raw)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if username != expected_user:
        raise RuntimeError(
            f"{env_name} must use PostgreSQL role {expected_user!r}, got {username!r}"
        )
    if not password:
        raise RuntimeError(f"{env_name} must include a password")
    return username, password


async def _ensure_login_role(conn: asyncpg.Connection, *, name: str, password: str) -> None:
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", name)
    ident = _quote_ident(name)
    password_sql = _quote_literal(password)
    statement = "ALTER ROLE" if exists else "CREATE ROLE"
    await conn.execute(
        f"{statement} {ident} WITH LOGIN PASSWORD {password_sql} "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )


async def provision(database_url: str) -> None:
    credentials = {
        role_name: _credentials(env_name, role_name)
        for role_name, env_name in RUNTIME_ROLES
    }
    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            current_user = str(await conn.fetchval("SELECT current_user"))
            database_name = str(await conn.fetchval("SELECT current_database()"))
            migrator_ident = _quote_ident(current_user)
            database_ident = _quote_ident(database_name)

            for role_name, _ in RUNTIME_ROLES:
                _, password = credentials[role_name]
                await _ensure_login_role(conn, name=role_name, password=password)

            role_list = ", ".join(_quote_ident(role_name) for role_name, _ in RUNTIME_ROLES)
            await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role_list}")
            await conn.execute(f"GRANT CONNECT ON DATABASE {database_ident} TO {role_list}")

            for role_name, _ in RUNTIME_ROLES:
                ident = _quote_ident(role_name)
                await conn.execute(
                    f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {ident}"
                )
                await conn.execute(
                    f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {ident}"
                )

            api = _quote_ident("rpy_api")
            await conn.execute(
                f"GRANT SELECT ON {', '.join(_quote_ident(t) for t in API_READ_TABLES)} TO {api}"
            )
            await conn.execute(
                f"GRANT INSERT, UPDATE ON processes, process_versions, tenant_judit_requests, public_summary_requests, judit_trackings, acquisition_authorizations TO {api}"
            )
            await conn.execute(
                "GRANT INSERT ON access_log, judit_deliveries, judit_request_completions, jobs, tenant_processes, tenant_judit_request_history TO "
                f"{api}"
            )

            worker = _quote_ident("rpy_worker")
            await conn.execute(f"GRANT SELECT, INSERT, UPDATE ON jobs TO {worker}")
            await conn.execute(f"GRANT SELECT, UPDATE ON processes, process_versions TO {worker}")
            await conn.execute(
                f"GRANT SELECT, UPDATE ON tenant_judit_requests, public_summary_requests TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, UPDATE ON judit_trackings, judit_tracking_refreshes TO {worker}"
            )
            await conn.execute(f"GRANT SELECT, INSERT ON tenant_processes TO {worker}")
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_steps TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE ON process_summaries TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_summary_sources TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_summary_attachment_sources TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_summary_claims, process_summary_claim_sources TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_summary_claim_evidence_excerpts TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_summary_glossary_sources TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_datajud_field_provenance TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_step_embeddings TO {worker}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON process_attachments, attachment_chunks TO {worker}"
            )

            scheduler = _quote_ident("rpy_scheduler")
            await conn.execute(f"GRANT SELECT, DELETE ON processes TO {scheduler}")
            await conn.execute(f"GRANT SELECT ON process_versions TO {scheduler}")
            await conn.execute(f"GRANT SELECT, INSERT, UPDATE ON jobs TO {scheduler}")
            await conn.execute(
                f"GRANT SELECT, UPDATE ON judit_trackings TO {scheduler}"
            )
            await conn.execute(
                f"GRANT SELECT, INSERT ON judit_tracking_refreshes TO {scheduler}"
            )
            await conn.execute(f"GRANT SELECT, DELETE ON judit_deliveries, jobs TO {scheduler}")
            await conn.execute(
                f"GRANT SELECT, DELETE ON judit_request_completions TO {scheduler}"
            )

            backup = _quote_ident("rpy_backup")
            await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {backup}")
            await conn.execute(f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {backup}")
            await conn.execute(f"GRANT INSERT ON backup_runs TO {backup}")

            await conn.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_ident} IN SCHEMA public "
                f"GRANT SELECT ON TABLES TO {backup}"
            )
            await conn.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_ident} IN SCHEMA public "
                f"GRANT SELECT ON SEQUENCES TO {backup}"
            )
    finally:
        await conn.close()


async def _main() -> None:
    database_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")
    await provision(database_url)


if __name__ == "__main__":
    asyncio.run(_main())
