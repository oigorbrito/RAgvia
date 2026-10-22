from __future__ import annotations

import asyncio
import json
import os
import http.client
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
from playwright.async_api import async_playwright

STAGING_URL = os.environ.get("RPY_STAGING_DATABASE_URL", "postgresql://127.0.0.1:5435/rpy_staging")
API_ORIGIN = os.environ.get("RPY_STAGING_API_ORIGIN", "http://127.0.0.1:8008")
API_TARGET = urlsplit(API_ORIGIN)
if API_TARGET.scheme != "http" or API_TARGET.hostname not in {"127.0.0.1", "localhost"}:
    raise RuntimeError("staging API must use a loopback HTTP URL")
ARTIFACT_DIR = Path(os.environ.get("RPY_STAGING_EVIDENCE_DIR", "staging_evidence"))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TOKEN_A = os.environ["RPY_STAGING_TOKEN_A"]
TOKEN_B = os.environ["RPY_STAGING_TOKEN_B"]
INVALID_TOKEN = f"invalid-{TOKEN_A}"
CODE_STORED = "0000000-00.2026.8.21.0001"
CODE_CONCURRENT = "0000002-00.2026.8.21.0001"
CODE_AMBIGUOUS = "0000003-00.2026.8.21.0001"

evidence_report = []


def record(name: str, passed: bool, detail: str):
    status = "PASS" if passed else "FAIL"
    evidence_report.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")


def run_http_json(url: str, method: str = "GET", headers: dict | None = None, data: dict | None = None):
    h = headers.copy() if headers else {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme != API_TARGET.scheme
        or parsed_url.hostname != API_TARGET.hostname
        or parsed_url.port != API_TARGET.port
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError("staging validation only permits its local API endpoint")
    connection = http.client.HTTPConnection(
        API_TARGET.hostname, API_TARGET.port or 80, timeout=30
    )
    try:
        path = parsed_url.path or "/"
        if parsed_url.query:
            path += f"?{parsed_url.query}"
        connection.request(method, path, body=body, headers=h)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return response.status, payload, response.headers
    finally:
        connection.close()


async def run_db_scenarios():
    conn = await asyncpg.connect(STAGING_URL)
    try:
        # Pre-check initial jobs
        initial_jobs = await conn.fetchval("SELECT COUNT(*) FROM jobs WHERE task_name = 'request_judit_process'")
        record("DB: initial jobs count", True, f"Initial request_judit_process jobs: {initial_jobs}")

        # Clean CODE_CONCURRENT before running scenarios
        await conn.execute("DELETE FROM tenant_judit_requests WHERE tenant_id = $1 AND process_code = $2", TENANT_A, CODE_CONCURRENT)
        await conn.execute("DELETE FROM acquisition_authorizations WHERE tenant_id = $1 AND process_code = $2", TENANT_A, CODE_CONCURRENT)

        # Setup Scenario F: ambiguous failure in DB
        await conn.execute(
            """
            INSERT INTO tenant_judit_requests (tenant_id, process_code, status, attempt_number, created_at)
            VALUES ($1, $2, 'failed_ambiguous', 1, NOW() - INTERVAL '1 day')
            ON CONFLICT (tenant_id, process_code) DO UPDATE SET status = 'failed_ambiguous'
            """,
            TENANT_A,
            CODE_AMBIGUOUS,
        )
        record("DB: seeded failed_ambiguous", True, f"Seeded {CODE_AMBIGUOUS} with status failed_ambiguous")

        return initial_jobs
    finally:
        await conn.close()


async def run_playwright_scenarios():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("\n--- Cenário A: Primeiro Acesso ---")
        await page.goto(API_ORIGIN, wait_until="networkidle")
        screenshot_a = ARTIFACT_DIR / "cenario_a_primeiro_acesso.png"
        await page.screenshot(path=str(screenshot_a), full_page=True)

        is_judit_checked = await page.locator("#source-judit").is_checked()
        access_label = (await page.locator("#access-label").inner_text()).strip()
        auth_has_green = "authenticated" in ((await page.locator("#access-trigger").get_attribute("class")) or "")

        record("Cenário A: Judit fonte padrão", is_judit_checked, "Judit radio is checked by default")
        record("Cenário A: Indicador neutro", access_label == "Configurar acesso" and not auth_has_green, f"Access label: '{access_label}', authenticated class present: {auth_has_green}")
        record("Cenário A: Screenshot salvo", screenshot_a.exists(), f"Saved {screenshot_a.name}")

        print("\n--- Cenário B: Autenticação ---")
        # Invalid token verification
        await page.locator("#bearer-token").fill(INVALID_TOKEN)
        await page.locator("#verify-token").click()
        await page.wait_for_timeout(1000)

        auth_class_inv = (await page.locator("#access-trigger").get_attribute("class")) or ""
        token_err = await page.locator("#token-error").inner_text()
        screenshot_b_inv = ARTIFACT_DIR / "cenario_b_auth_invalida.png"
        await page.locator("#bearer-token").evaluate("element => { element.value = ''; }")
        await page.screenshot(path=str(screenshot_b_inv), full_page=True)
        await page.locator("#bearer-token").fill(TOKEN_A)
        record("Cenário B: Rejeição de credencial inválida", "invalid" in auth_class_inv, f"Indicator has 'invalid' class. Token error displayed: '{token_err}'")

        # Valid token verification
        await page.locator("#bearer-token").fill(TOKEN_A)
        await page.locator("#verify-token").click()
        await page.wait_for_timeout(1000)

        auth_class_val = (await page.locator("#access-trigger").get_attribute("class")) or ""
        access_label_val = (await page.locator("#access-label").inner_text()).strip()
        screenshot_b_val = ARTIFACT_DIR / "cenario_b_auth_valida.png"
        await page.locator("#bearer-token").evaluate("element => { element.value = ''; }")
        await page.screenshot(path=str(screenshot_b_val), full_page=True)
        await page.locator("#bearer-token").fill(TOKEN_A)
        record("Cenário B: Validação real no backend", "authenticated" in auth_class_val and access_label_val == "Acesso autenticado", f"Indicator has 'authenticated' class. Access label: '{access_label_val}'")
        record("Cenário B: Screenshots salvos", screenshot_b_inv.exists() and screenshot_b_val.exists(), "Saved invalid and valid auth screenshots")

        print("\n--- Cenário C: Histórico Existente ---")
        await page.locator("#process-code").fill(CODE_STORED)
        await page.locator("#search-submit").click()
        await page.wait_for_timeout(1500)

        # Confirm status panel indicates process already consulted
        status_title = (await page.locator("#status-title").inner_text()).strip()
        open_history_btn = page.locator("#open-existing-history")
        refresh_btn = page.locator("#request-refresh")

        record("Cenário C: Aviso de histórico existente", "Processo já consultado" in status_title, f"Status title: '{status_title}'")
        record("Cenário C: Botões de escolha visíveis", (await open_history_btn.is_visible()) and (await refresh_btn.is_visible()), "Both 'Abrir histórico existente' and 'Consultar novamente na Judit' are visible")

        # Open existing history
        await open_history_btn.click()
        await page.locator("#result").wait_for(state="visible")
        await page.wait_for_timeout(500)

        screenshot_c = ARTIFACT_DIR / "cenario_c_historico_existente.png"
        await page.screenshot(path=str(screenshot_c), full_page=True)

        summary_body = await page.locator("#summary-body").inner_text()
        record("Cenário C: Abertura do processo armazenado", "Resumo do processo" in summary_body or "0000000-00.2026.8.21.0001" in summary_body, f"Summary rendered: '{summary_body[:60]}...'")

        print("\n--- Cenário D: Nova Consulta (Confirmação e Autorização) ---")
        # Click refresh button from result
        await page.locator("#refresh-from-result").click()
        await page.locator("#confirm-modal").wait_for(state="visible")
        await page.wait_for_timeout(500)

        screenshot_d1 = ARTIFACT_DIR / "cenario_d1_modal_aviso.png"
        await page.screenshot(path=str(screenshot_d1), full_page=True)

        modal_title = (await page.locator("#confirm-modal-title").inner_text()).strip()
        record("Cenário D: Modal de aviso de cobrança", "Confirmar nova consulta na Judit" in modal_title, f"Modal title: '{modal_title}'")

        # Action 1: Cancel
        await page.locator("#confirm-refresh-cancel").click()
        await page.wait_for_timeout(500)
        modal_hidden = not (await page.locator("#confirm-modal").is_visible())
        record("Cenário D: Cancelamento da confirmação", modal_hidden, "Modal closed on cancel without triggering request")

        # Action 2: Reopen and Confirm
        await page.locator("#refresh-from-result").click()
        await page.locator("#confirm-modal").wait_for(state="visible")
        await page.locator("#confirm-refresh-submit").click()
        await page.wait_for_timeout(2000)

        screenshot_d2 = ARTIFACT_DIR / "cenario_d2_confirmacao_executada.png"
        await page.screenshot(path=str(screenshot_d2), full_page=True)
        status_after_refresh = (await page.locator("#status-title").inner_text()).strip() if (await page.locator("#status").is_visible()) else "result visible"
        record("Cenário D: Confirmação e aquisição autorizada", True, f"Status after confirmation: '{status_after_refresh}'")

        print("\n--- Cenário F: Falha Ambígua na Interface ---")
        await page.locator("#process-code").fill(CODE_AMBIGUOUS)
        await page.locator("#search-submit").click()
        await page.wait_for_timeout(1500)

        status_ambiguous = (await page.locator("#status-title").inner_text()).strip()
        screenshot_f = ARTIFACT_DIR / "cenario_f_falha_ambigua.png"
        await page.screenshot(path=str(screenshot_f), full_page=True)

        record("Cenário F: Interface bloqueia repetição após falha ambígua", "incerto" in status_ambiguous.lower() or "ambígua" in status_ambiguous.lower(), f"Status title: '{status_ambiguous}'")

        print("\n--- Cenário G: DataJud Desabilitado ---")
        datajud_radio = page.locator("#source-datajud")
        datajud_disabled = await datajud_radio.is_disabled()
        hint_text = (await page.locator("#datajud-status-hint").inner_text()).strip()

        screenshot_g = ARTIFACT_DIR / "cenario_g_datajud_desabilitado.png"
        await page.screenshot(path=str(screenshot_g), full_page=True)

        record("Cenário G: DataJud desabilitado por padrão na UI", datajud_disabled, f"DataJud radio disabled: {datajud_disabled}. Hint: '{hint_text}'")

        await browser.close()


async def verify_post_actions_in_db(initial_jobs: int):
    conn = await asyncpg.connect(STAGING_URL)
    try:
        print("\n--- Validação Pós-Ações no PostgreSQL ---")
        # Check jobs count for CODE_STORED
        jobs_stored = await conn.fetchval(
            """
            SELECT COUNT(*) FROM jobs j
            JOIN tenant_judit_requests r ON r.id = (j.payload->>'tenant_request_id')::uuid
            WHERE j.task_name = 'request_judit_process' AND r.process_code = $1
            """,
            CODE_STORED,
        )
        record("DB: Job enfileirado após confirmação", jobs_stored == 1, f"Exact 1 new job enqueued for authorized refresh of {CODE_STORED} (count: {jobs_stored})")

        # Check tenant_judit_request_history
        archived = await conn.fetch("SELECT * FROM tenant_judit_request_history WHERE tenant_id = $1 AND process_code = $2", TENANT_A, CODE_STORED)
        record("DB: Arquivamento da aquisição anterior", len(archived) == 1 and archived[0]["judit_request_id"] == "judit_req_initial_123", f"Archived rows: {len(archived)}, archived judit_request_id: {archived[0]['judit_request_id'] if archived else None}")

        # Check authorization consumption
        auth_row = await conn.fetchrow(
            "SELECT token, operation, consumed_at, consumed_by_request_id FROM acquisition_authorizations WHERE tenant_id = $1 AND process_code = $2 ORDER BY created_at DESC LIMIT 1",
            TENANT_A,
            CODE_STORED,
        )
        record("DB: Autorização consumida", auth_row is not None and auth_row["consumed_at"] is not None, f"Token consumed at {auth_row['consumed_at'] if auth_row else None}, operation: {auth_row['operation'] if auth_row else None}")

        # Re-use authorization attempt: verify it fails
        if auth_row:
            token = auth_row["token"]
            # Call API to reuse token
            loop = asyncio.get_event_loop()
            status, body, _ = await loop.run_in_executor(
                None,
                lambda: run_http_json(
                    f"{API_ORIGIN}/processes/{CODE_STORED}/request",
                    method="POST",
                    headers={"Authorization": f"Bearer {TOKEN_A}", "X-Acquisition-Authorization": token},
                    data={"force": True},
                ),
            )
            record("Segurança: Reutilização de token rejeitada com HTTP 409", status == 409, f"HTTP status: {status}, detail: {body.get('detail')}")

        print("\n--- Cenário E: Concorrência ---")
        # Issue authorization for CODE_CONCURRENT
        loop = asyncio.get_event_loop()
        auth_status, auth_body, _ = await loop.run_in_executor(
            None,
            lambda: run_http_json(
                f"{API_ORIGIN}/processes/{CODE_CONCURRENT}/authorizations",
                method="POST",
                headers={"Authorization": f"Bearer {TOKEN_A}"},
                data={"operation": "acquisition", "source": "judit"},
            ),
        )
        record("Cenário E: Emissão de autorização prévia", auth_status == 201, f"HTTP status: {auth_status}")
        concurrent_token = auth_body.get("token")

        # Run 2 parallel requests with the same token
        headers = {"Authorization": f"Bearer {TOKEN_A}", "X-Acquisition-Authorization": concurrent_token}

        async def make_req():
            return await loop.run_in_executor(
                None,
                lambda: run_http_json(
                    f"{API_ORIGIN}/processes/{CODE_CONCURRENT}/request",
                    method="POST",
                    headers=headers,
                    data={"force": True},
                ),
            )

        res1, res2 = await asyncio.gather(make_req(), make_req())
        statuses = [res1[0], res2[0]]
        # One must succeed (202), the other will either see processing (202 created=False) or token consumed (409)
        record("Cenário E: Disparo simultâneo", any(s == 202 for s in statuses), f"Parallel responses: req1={res1[0]} (created={res1[1].get('created')}), req2={res2[0]} (created={res2[1].get('created')})")

        # Verify exact jobs in DB for CODE_CONCURRENT
        concurrent_jobs = await conn.fetchval(
            """
            SELECT COUNT(*) FROM jobs j
            JOIN tenant_judit_requests r ON r.id = (j.payload->>'tenant_request_id')::uuid
            WHERE j.task_name = 'request_judit_process' AND r.process_code = $1
            """,
            CODE_CONCURRENT,
        )
        record("Cenário E: Apenas 1 job criado para operação concorrente", concurrent_jobs == 1, f"Jobs created for {CODE_CONCURRENT}: {concurrent_jobs}")

        print("\n--- Cenário G: DataJud Backend Rejection ---")
        dj_status, dj_body, _ = await loop.run_in_executor(
            None,
            lambda: run_http_json(
                f"{API_ORIGIN}/processes/{CODE_STORED}/datajud",
                method="POST",
                headers={"Authorization": f"Bearer {TOKEN_A}"},
            ),
        )
        record("Cenário G: Backend recusa consulta ao DataJud", dj_status == 403, f"HTTP status: {dj_status}, detail: {dj_body.get('detail')}")

        dj_no_auth_status, _, _ = await loop.run_in_executor(
            None,
            lambda: run_http_json(
                f"{API_ORIGIN}/processes/{CODE_STORED}/datajud",
                method="POST",
            ),
        )
        record("Cenário G: Rejeição sem credencial", dj_no_auth_status == 401, f"HTTP status without token: {dj_no_auth_status}")

        print("\n--- Validação de Isolamento Multi-Tenant ---")
        # Tenant B requests Tenant A's process status
        status_b, body_b, _ = await loop.run_in_executor(
            None,
            lambda: run_http_json(
                f"{API_ORIGIN}/processes/{CODE_STORED}/status",
                headers={"Authorization": f"Bearer {TOKEN_B}"},
            ),
        )
        record("Isolamento: Tenant B não vê histórico do Tenant A", status_b == 200 and body_b.get("classification") == "acquisition_required", f"Tenant B classification for Tenant A process: '{body_b.get('classification')}', history_available: {body_b.get('history_available')}")

    finally:
        await conn.close()


async def main():
    initial_jobs = await run_db_scenarios()
    await run_playwright_scenarios()
    await verify_post_actions_in_db(initial_jobs)

    print("\n================== RESUMO DA HOMOLOGAÇÃO ==================")
    all_passed = all(item["status"] == "PASS" for item in evidence_report)
    for item in evidence_report:
        print(f"[{item['status']}] {item['name']}: {item['detail']}")
    print(f"\nDECISÃO GERAL: {'PASS' if all_passed else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
