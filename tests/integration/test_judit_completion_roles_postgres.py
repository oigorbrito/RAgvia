from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import asyncpg
import pytest

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
async def test_completion_marker_runtime_roles_are_provisioned(monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_request_completions', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_request_completions', 'INSERT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'judit_request_completions', 'DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_request_completions', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'judit_request_completions', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'judit_request_completions', 'SELECT')"
        )
    finally:
        await admin.close()
