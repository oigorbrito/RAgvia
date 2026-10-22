from pathlib import Path

import pytest

from scripts.validate_candidate_postgres import check_results, wait_for_sql


@pytest.mark.parametrize("attributes", [
    'tests="167" skipped="1"',
    'tests="167" errors="10" failures="1"',
    'tests="0"',
])
def test_qualification_rejects_skips_errors_and_empty_suite(tmp_path: Path, attributes: str):
    report = tmp_path / "results.xml"
    report.write_text(f"<testsuites><testsuite {attributes}/></testsuites>")
    with pytest.raises(RuntimeError, match="qualification rejected"):
        check_results(report)


def test_qualification_accepts_all_executed_tests(tmp_path: Path):
    report = tmp_path / "results.xml"
    report.write_text('<testsuites><testsuite tests="167"/></testsuites>')
    assert check_results(report) == {"tests": 167, "failures": 0, "errors": 0, "skipped": 0}


async def test_readiness_uses_sql_and_closes_connection(monkeypatch):
    calls = []

    class Connection:
        async def fetchval(self, sql):
            calls.append(sql)
            return 1

        async def close(self):
            calls.append("closed")

    async def connect(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("startup")
        return Connection()

    monkeypatch.setattr("scripts.validate_candidate_postgres.asyncpg.connect", connect)
    await wait_for_sql("postgresql://loopback-fixture", timeout=2)
    assert calls == ["postgresql://loopback-fixture", "postgresql://loopback-fixture", "SELECT 1", "closed"]


async def test_readiness_has_a_deadline(monkeypatch):
    async def connect(*args, **kwargs):
        raise ConnectionError("not ready")

    monkeypatch.setattr("scripts.validate_candidate_postgres.asyncpg.connect", connect)
    with pytest.raises(TimeoutError):
        await wait_for_sql("postgresql://loopback-fixture", timeout=0.01)
