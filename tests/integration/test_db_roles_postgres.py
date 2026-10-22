from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.claim_evidence import load_summary_claim_evidence
from app.migrations import migrate

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "provision_db_roles.py"
spec = importlib.util.spec_from_file_location("provision_db_roles", MODULE_PATH)
assert spec is not None and spec.loader is not None
roles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roles)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_runtime_database_roles_are_least_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    import urllib.parse
    def _swap_creds(url: str, user: str, pwd: str) -> str:
        parsed = urllib.parse.urlparse(url)
        netloc = f"{user}:{pwd}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))

    urls = {
        "API_DATABASE_URL": _swap_creds(TEST_DATABASE_URL, "rpy_api", "api-password"),
        "WORKER_DATABASE_URL": _swap_creds(TEST_DATABASE_URL, "rpy_worker", "worker-password"),
        "SCHEDULER_DATABASE_URL": _swap_creds(TEST_DATABASE_URL, "rpy_scheduler", "scheduler-password"),
        "BACKUP_DATABASE_URL": _swap_creds(TEST_DATABASE_URL, "rpy_backup", "backup-password"),
    }
    for name, value in urls.items():
        monkeypatch.setenv(name, value)

    await roles.provision(TEST_DATABASE_URL)

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await admin.fetch(
            """
            SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = ANY($1::text[])
            ORDER BY rolname
            """,
            ["rpy_api", "rpy_worker", "rpy_scheduler", "rpy_backup"],
        )
        assert len(rows) == 4
        for row in rows:
            assert row["rolsuper"] is False
            assert row["rolcreatedb"] is False
            assert row["rolcreaterole"] is False
            assert row["rolreplication"] is False
            assert row["rolbypassrls"] is False

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'processes', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_summary_attachment_sources', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_summary_claims', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_summary_claim_sources', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_summary_claim_evidence_excerpts', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'access_log', 'INSERT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'public_summary_requests', 'SELECT,INSERT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_trackings', 'SELECT,INSERT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'acquisition_authorizations', 'SELECT,INSERT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'tenant_judit_request_history', 'SELECT,INSERT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_tracking_refreshes', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_tracking_refreshes', 'UPDATE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'public_summary_requests', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'processes', 'DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_attachment_status_counts', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_attachments', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'attachment_chunks', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_schema_privilege('rpy_api', 'public', 'CREATE')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'jobs', 'UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_steps', 'DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'public_summary_requests', 'SELECT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'tenant_judit_requests', 'SELECT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'judit_trackings', 'SELECT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'judit_tracking_refreshes', 'SELECT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'tenant_processes', 'SELECT,INSERT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_attachments', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'attachment_chunks', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'process_attachment_status_counts', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_summary_attachment_sources', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_summary_claims', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_summary_claim_sources', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_summary_claim_evidence_excerpts', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_datajud_field_provenance', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'process_datajud_field_provenance', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'process_datajud_field_provenance', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'public_summary_requests', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'processes', 'DELETE')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'processes', 'DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'jobs', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_trackings', 'SELECT,UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_tracking_refreshes', 'SELECT,INSERT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_deliveries', 'SELECT,DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_tracking_refreshes', 'UPDATE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'process_summaries', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'attachment_chunks', 'SELECT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'process_summary_attachment_sources', 'SELECT')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'processes', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'process_attachments', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'attachment_chunks', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'process_summary_attachment_sources', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'process_summary_claim_evidence_excerpts', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'backup_runs', 'INSERT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'processes', 'UPDATE')"
        )
    finally:
        await admin.close()

    api = await asyncpg.connect(urls["API_DATABASE_URL"])
    try:
        assert await api.fetchval("SELECT count(*) FROM processes") is not None
        assert await api.fetchval("SELECT count(*) FROM public_summary_requests") is not None
        assert await api.fetchval("SELECT count(*) FROM judit_trackings") is not None
        assert await api.fetchval("SELECT count(*) FROM process_attachment_status_counts") is not None
        assert await api.fetchval("SELECT count(*) FROM process_summary_attachment_sources") is not None
        assert await api.fetchval("SELECT count(*) FROM process_summary_claims") is not None
        assert await api.fetchval("SELECT count(*) FROM process_summary_claim_sources") is not None
        assert await load_summary_claim_evidence(
            api,
            summary_id=uuid4(),
        ) == []
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetchval(
                "SELECT count(*) FROM process_summary_claim_evidence_excerpts"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetchval("SELECT count(*) FROM process_datajud_field_provenance")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetchval("SELECT count(*) FROM attachment_chunks")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("CREATE TABLE db_role_escape_probe (id integer)")
    finally:
        await api.close()
