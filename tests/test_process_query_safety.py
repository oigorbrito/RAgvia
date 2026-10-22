from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.process_query_safety import (
    ACQUISITION_PENDING,
    ACQUISITION_REQUIRED,
    AMBIGUOUS_FAILURE,
    READ_EXISTING,
    REFRESH_REQUIRES_CONFIRMATION,
    AcquisitionAuthorization,
    AmbiguousFailureError,
    AuthorizationAlreadyConsumedError,
    AuthorizationExpiredError,
    CnjStatusResult,
    InvalidAuthorizationError,
    ProcessQuerySafetyError,
    RefreshRequiresConfirmationError,
    classify_cnj_status,
    consume_authorization,
    create_authorization,
)


class ProcessQuerySafetyUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_classifications_constants(self):
        self.assertEqual(READ_EXISTING, "read_existing")
        self.assertEqual(ACQUISITION_PENDING, "acquisition_pending")
        self.assertEqual(ACQUISITION_REQUIRED, "acquisition_required")
        self.assertEqual(REFRESH_REQUIRES_CONFIRMATION, "refresh_requires_confirmation")
        self.assertEqual(AMBIGUOUS_FAILURE, "ambiguous_failure")

    def test_status_result_to_dict_preserves_distinct_timestamps(self):
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=2)
        version_time = now - timedelta(hours=1)

        result = CnjStatusResult(
            code="0000000-00.2024.8.26.0100",
            classification=READ_EXISTING,
            history_available=True,
            source="judit",
            last_acquisition_at=earlier,
            version_date=version_time,
            process_updated_at=now,
        )
        data = result.to_dict()
        self.assertEqual(data["code"], "0000000-00.2024.8.26.0100")
        self.assertEqual(data["classification"], "read_existing")
        self.assertEqual(data["status"], "read_existing")
        self.assertTrue(data["history_available"])
        self.assertEqual(data["last_acquisition_at"], earlier.isoformat())
        self.assertEqual(data["version_date"], version_time.isoformat())
        self.assertEqual(data["process_updated_at"], now.isoformat())

    def test_error_hierarchy(self):
        self.assertTrue(issubclass(AmbiguousFailureError, ProcessQuerySafetyError))
        self.assertTrue(issubclass(RefreshRequiresConfirmationError, ProcessQuerySafetyError))
        self.assertTrue(issubclass(InvalidAuthorizationError, ProcessQuerySafetyError))
        self.assertTrue(issubclass(AuthorizationAlreadyConsumedError, ProcessQuerySafetyError))
        self.assertTrue(issubclass(AuthorizationExpiredError, ProcessQuerySafetyError))

    async def test_classify_cnj_status_acquisition_required(self):
        conn = AsyncMock()
        conn.fetchrow.side_effect = [None, None]  # process_row=None, request_row=None
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        res = await classify_cnj_status(conn, tenant_id=tenant_id, code=code)
        self.assertEqual(res.classification, ACQUISITION_REQUIRED)
        self.assertFalse(res.history_available)
        self.assertIsNone(res.last_acquisition_at)
        self.assertIsNone(res.version_date)
        self.assertIsNone(res.process_updated_at)

    async def test_classify_cnj_status_acquisition_pending(self):
        conn = AsyncMock()
        now = datetime.now(timezone.utc)
        conn.fetchrow.side_effect = [
            None,  # process_row=None
            {
                "id": uuid4(),
                "status": "processing",
                "judit_request_id": None,
                "created_at": now,
                "completed_at": None,
                "attempt_number": 1,
            },
        ]
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        res = await classify_cnj_status(conn, tenant_id=tenant_id, code=code)
        self.assertEqual(res.classification, ACQUISITION_PENDING)
        self.assertFalse(res.history_available)
        self.assertEqual(res.last_acquisition_at, now)

    async def test_classify_cnj_status_ambiguous_failure(self):
        conn = AsyncMock()
        now = datetime.now(timezone.utc)
        conn.fetchrow.side_effect = [
            None,
            {
                "id": uuid4(),
                "status": "failed_ambiguous",
                "judit_request_id": None,
                "created_at": now,
                "completed_at": None,
                "attempt_number": 1,
            },
        ]
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        res = await classify_cnj_status(conn, tenant_id=tenant_id, code=code)
        self.assertEqual(res.classification, AMBIGUOUS_FAILURE)
        self.assertFalse(res.history_available)

    async def test_classify_cnj_status_read_existing(self):
        conn = AsyncMock()
        now = datetime.now(timezone.utc)
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "updated_at": now,
                "version_created_at": now,
                "finalized_at": now,
            },
            {
                "id": uuid4(),
                "status": "completed",
                "judit_request_id": "req-1",
                "created_at": now,
                "completed_at": now,
                "attempt_number": 1,
            },
        ]
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        res = await classify_cnj_status(conn, tenant_id=tenant_id, code=code)
        self.assertEqual(res.classification, READ_EXISTING)
        self.assertTrue(res.history_available)

    async def test_classify_cnj_status_refresh_requires_confirmation_on_refresh_op(self):
        conn = AsyncMock()
        now = datetime.now(timezone.utc)
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "updated_at": now,
                "version_created_at": now,
                "finalized_at": now,
            },
            {
                "id": uuid4(),
                "status": "completed",
                "judit_request_id": "req-1",
                "created_at": now,
                "completed_at": now,
                "attempt_number": 1,
            },
        ]
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        res = await classify_cnj_status(conn, tenant_id=tenant_id, code=code, operation="refresh")
        self.assertEqual(res.classification, REFRESH_REQUIRES_CONFIRMATION)
        self.assertTrue(res.history_available)

    async def test_create_authorization_validates_parameters(self):
        conn = AsyncMock()
        tenant_id = uuid4()
        code = "0000000-00.2024.8.26.0100"

        with self.assertRaises(ValueError):
            await create_authorization(conn, tenant_id=tenant_id, code=code, source="invalid")

        with self.assertRaises(ValueError):
            await create_authorization(conn, tenant_id=tenant_id, code=code, operation="invalid")

    def test_sao_paulo_timezone_civil_day_detection(self):
        from zoneinfo import ZoneInfo
        from app.process_query_safety import (
            get_sao_paulo_civil_date,
            is_acquired_today_sao_paulo,
        )

        sp_tz = ZoneInfo("America/Sao_Paulo")
        now_sp = datetime.now(sp_tz)
        today_utc = now_sp.astimezone(timezone.utc)
        self.assertTrue(is_acquired_today_sao_paulo(today_utc))
        self.assertEqual(get_sao_paulo_civil_date(today_utc), now_sp.date().isoformat())

        past_utc = today_utc - timedelta(days=2)
        self.assertFalse(is_acquired_today_sao_paulo(past_utc))
        self.assertFalse(is_acquired_today_sao_paulo(None))
        self.assertIsNone(get_sao_paulo_civil_date(None))

    async def test_classify_cnj_status_advisory_messages(self):
        conn = AsyncMock()
        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=10)

        # 1. Process exists and was acquired today
        conn.fetchrow.side_effect = [
            {"id": uuid4(), "updated_at": now, "version_created_at": now, "finalized_at": now},
            {"id": uuid4(), "status": "completed", "judit_request_id": "r1", "created_at": now, "completed_at": now, "attempt_number": 1},
        ]
        res_today = await classify_cnj_status(conn, tenant_id=uuid4(), code="0000000-00.2024.8.26.0100")
        self.assertTrue(res_today.acquired_today)
        self.assertIn("consultado hoje", res_today.advisory)

        # 2. Process exists but acquired 10 days ago
        conn.fetchrow.side_effect = [
            {"id": uuid4(), "updated_at": old_date, "version_created_at": old_date, "finalized_at": old_date},
            {"id": uuid4(), "status": "completed", "judit_request_id": "r1", "created_at": old_date, "completed_at": old_date, "attempt_number": 1},
        ]
        res_old = await classify_cnj_status(conn, tenant_id=uuid4(), code="0000000-00.2024.8.26.0100")
        self.assertFalse(res_old.acquired_today)
        self.assertIn("histórico disponível", res_old.advisory)

    def test_list_processes_unauthorized_fails_closed(self):
        from fastapi.testclient import TestClient
        from app.api import app

        client = TestClient(app)
        response = client.get("/processes")
        self.assertEqual(response.status_code, 401)

    def test_list_processos_v1_unauthorized_fails_closed(self):
        from fastapi.testclient import TestClient
        from app.api import app

        client = TestClient(app)
        response = client.get("/v1/processos")
        self.assertEqual(response.status_code, 401)

    def test_auth_verify_endpoint_unauthorized_and_authorized(self):
        from fastapi.testclient import TestClient
        from app.api import app

        tenant_id = uuid4()
        app.state.bearer_tokens = {"valid-auth-token": tenant_id}
        client = TestClient(app)

        # 1. Sem token -> 401
        res_unauth = client.get("/auth/verify")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Token inválido -> 401
        res_invalid = client.get("/auth/verify", headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(res_invalid.status_code, 401)

        # 3. Token válido -> 200
        res_auth = client.get("/auth/verify", headers={"Authorization": "Bearer valid-auth-token"})
        self.assertEqual(res_auth.status_code, 200)
        data = res_auth.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data.get("tenant_id"), str(tenant_id))

    def test_auth_verificar_v1_endpoint_unauthorized_and_authorized(self):
        from fastapi.testclient import TestClient
        from app.api import app

        tenant_id = uuid4()
        app.state.bearer_tokens = {"valid-auth-token-v1": tenant_id}
        client = TestClient(app)

        res_unauth = client.get("/v1/auth/verificar")
        self.assertEqual(res_unauth.status_code, 401)

        res_auth = client.get("/v1/auth/verificar", headers={"Authorization": "Bearer valid-auth-token-v1"})
        self.assertEqual(res_auth.status_code, 200)
        data = res_auth.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data.get("tenant_id"), str(tenant_id))

    def test_capabilities_endpoint_public_contract_and_leak_free(self):
        from fastapi.testclient import TestClient
        from app.api import app

        client = TestClient(app)
        for path in ("/capabilities", "/v1/capacidades", "/v1/capabilities"):
            res = client.get(path)
            self.assertEqual(res.status_code, 200, f"failed for {path}")
            data = res.json()
            self.assertEqual(data.get("default_source"), "judit")
            self.assertFalse(data.get("authenticated"))

            sources = data.get("sources", {})
            self.assertIn("judit", sources)
            self.assertTrue(sources["judit"]["enabled"])
            self.assertFalse(sources["judit"]["experimental"])

            self.assertIn("datajud", sources)
            self.assertFalse(sources["datajud"]["enabled"])
            self.assertTrue(sources["datajud"]["experimental"])
            self.assertIn("DATAJUD_ENABLED=false", sources["datajud"]["reason_unavailable"])

            # Provar ausência de segredos ou credenciais expostas
            raw_text = res.text.lower()
            self.assertNotIn("datajud_api_key", raw_text)
            self.assertNotIn("judit_api_key", raw_text)
            self.assertNotIn("secret", raw_text)

    def test_capabilities_authenticated_tenant(self):
        from fastapi.testclient import TestClient
        from app.api import app

        tenant_id = uuid4()
        app.state.bearer_tokens = {"valid-cap-token": tenant_id}
        client = TestClient(app)

        res = client.get("/capabilities", headers={"Authorization": "Bearer valid-cap-token"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data.get("tenant_id"), str(tenant_id))

    def test_datajud_process_endpoints_fail_closed_and_forbidden_when_disabled(self):
        from fastapi.testclient import TestClient
        from app.api import app

        tenant_id = uuid4()
        app.state.bearer_tokens = {"datajud-token": tenant_id}
        client = TestClient(app)

        test_paths = (
            "/processes/0000000-00.2024.8.26.0100/datajud",
            "/v1/processos/0000000-00.2024.8.26.0100/datajud",
        )
        for path in test_paths:
            # Sem token -> 401
            res_unauth = client.get(path)
            self.assertEqual(res_unauth.status_code, 401)

            # Com token, mas DataJud desabilitado por padrão no servidor -> 403 Forbidden
            res_auth = client.get(path, headers={"Authorization": "Bearer datajud-token"})
            self.assertEqual(res_auth.status_code, 403)
            self.assertIn("desabilitado", res_auth.json().get("detail", "").lower())


if __name__ == "__main__":
    unittest.main()
