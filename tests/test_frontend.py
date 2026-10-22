from pathlib import Path
import unittest
from fastapi.testclient import TestClient
from app.api import app
FRONTEND=Path(__file__).parents[1]/"app"/"frontend"
def test_frontend_routes_are_served_without_database_lifespan()->None:
 checks=unittest.TestCase()
 client=TestClient(app); index=client.get("/")
 checks.assertEqual(index.status_code,200)
 for text in ("RagV","Número do processo","Bearer token","Ir para o conteúdo","Contexto do processo","Últimos acontecimentos"):
  checks.assertIn(text,index.text)
 checks.assertIn('aria-live="polite"',index.text)
 for path in ("/app.css","/config.js","/app.js","/favicon.svg"):
  checks.assertEqual(client.get(path).status_code,200)
def test_frontend_assets_require_revalidation()->None:
 client=TestClient(app)
 checks=unittest.TestCase()
 for path in ("/","/app.css","/config.js","/app.js","/favicon.svg"):
  checks.assertEqual(client.get(path).headers["cache-control"],"no-cache, must-revalidate")


def test_frontend_uses_canonical_ragv_identity_and_configure_access_panel()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8")
 checks=unittest.TestCase()
 checks.assertIn("<title>RagV</title>",html)
 checks.assertIn("aria-label=\"RagV — início\"",html)
 checks.assertNotIn("Rpy",html)
 checks.assertNotIn("Inteligência Processual",html)
 checks.assertIn('id="auth-panel" class="auth-panel" aria-labelledby="access-title" hidden',html)
 checks.assertIn('id="history-trigger"',html)
 checks.assertIn('id="history-panel"',html)
 checks.assertIn('href="favicon.svg"',html)


def test_provider_names_have_exact_accessible_tooltips_without_static_explanations()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8")
 checks=unittest.TestCase()
 checks.assertIn("Consulta processual para uso operacional. Pode gerar cobrança conforme o serviço contratado.",html)
 checks.assertIn("API pública do CNJ para consulta de metadados processuais. Pode auxiliar em testes de conectividade, conforme a cobertura disponível.",html)
 checks.assertEqual(html.count('class="provider-info"'),2)
 checks.assertIn('id="judit-info"',html)
 checks.assertIn('id="datajud-info"',html)
 checks.assertNotIn('class="source-desc"',html)
 checks.assertNotIn("DATAJUD_ENABLED=false",html)
 checks.assertNotIn("DataJud desabilitado",html)
 checks.assertNotIn("Fonte completa para busca processual",html)
 checks.assertNotIn("Base pública do CNJ (metadados textuais). Não substitui",html)
 checks.assertIn('value="judit" checked',html)
 checks.assertIn('value="datajud" disabled',html)


def test_frontend_does_not_persist_bearer_token_or_render_model_html()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "localStorage" not in source; assert "sessionStorage" not in source; assert "innerHTML" not in source; assert "textContent" in source; assert "Authorization" in source
def test_frontend_has_explicit_accessibility_and_state_contracts()->None:
 javascript=(FRONTEND/"app.js").read_text(encoding="utf-8"); stylesheet=(FRONTEND/"app.css").read_text(encoding="utf-8"); html=(FRONTEND/"index.html").read_text(encoding="utf-8"); assert "aria-invalid" in javascript; assert "aria-busy" in javascript; assert "Sem conexão com o serviço" in javascript; assert "navigator.clipboard.writeText" in javascript; assert "prefers-reduced-motion" in javascript; assert ":focus-visible" in stylesheet; assert "prefers-reduced-motion" in stylesheet; assert 'id="action-feedback"' in html; assert 'role="status"' in html; assert "Resumo copiado para a área de transferência." in javascript
def test_frontend_renders_detail_data_as_text_nodes()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "renderParties" in source; assert "renderSubjects" in source; assert "renderHeader" in source; assert "renderSteps" in source; assert "recent_steps" in source; assert "createElement('time')" in source
def test_frontend_formats_cnj_money_instance_and_party_role_for_legal_reading()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); html=(FRONTEND/"index.html").read_text(encoding="utf-8"); assert "formatCnjInput" in source; assert "formatCnj(data.code)" in source; assert "Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'})" in source; assert "Polo ativo" in source; assert "Polo passivo" in source; assert "ª instância" in source; assert "formatação CNJ é aplicada automaticamente" in html
def test_frontend_maps_public_summary_states_without_job_details()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "summary_status" in source; assert "preparação em andamento" in source; assert "resumo indisponível" in source; assert "sem resumo publicado" in source; assert "error_log" not in source; assert "max_attempts" not in source
def test_frontend_requests_missing_process_only_after_explicit_action()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8"); source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert 'id="request-process"' in html; assert "Solicitar consulta" in html; assert "pendingRequest={code,token}" in source; assert "Processo ainda não disponível" in source; assert "requestButton.addEventListener('click'" in source; assert "/request" in source; assert "method:'POST'" in source
def test_frontend_explains_long_running_request_and_polls_boundedly()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "MAX_POLL_ATTEMPTS=20" in source; assert "POLL_INTERVAL_MS=3000" in source; assert "setTimeout" in source; assert "setInterval" not in source; assert "pagehide" in source; assert "não é necessário manter esta página aberta" in source; assert "consultar este CNJ novamente mais tarde" in source
def test_frontend_never_exposes_provider_request_identifier()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "request_id" not in source; assert "judit_request_id" not in source
def test_frontend_follows_processing_summary_without_blocking_process_details()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "MAX_SUMMARY_POLL_ATTEMPTS=20" in source; assert "SUMMARY_POLL_INTERVAL_MS=3000" in source; assert "scheduleSummaryCheck" in source; assert "checkSummary" in source; assert "Os dados processuais já estão disponíveis" in source; assert "O resumo desta versão foi atualizado automaticamente" in source; assert "data.summary_status==='processing'" in source
def test_summary_followup_stops_on_terminal_state_and_keeps_manual_retry()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "data.summary_status!=='processing'" in source; assert "O resumo continua em preparação" in source; assert "retry:true" in source; assert "stopPolling()" in source
def test_process_document_has_internal_navigation_and_source_ai_distinction()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8"); css=(FRONTEND/"app.css").read_text(encoding="utf-8"); assert 'aria-label="Seções do processo"' in html; assert 'href="#summary-title"' in html; assert 'href="#case-data-title"' in html; assert 'href="#movements-title"' in html; assert "SÍNTESE GERADA POR IA" in html; assert "DADOS DA FONTE PROCESSUAL" in html; assert ".document-nav" in css; assert "position:sticky" in css
def test_frontend_formats_legal_data_without_mutating_api_contract()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "function formatCnj" in source; assert "function formatAmount" in source; assert "currency:'BRL'" in source; assert "Valor da causa" in source; assert "Polo ativo" in source; assert "Polo passivo" in source; assert "Pessoa física" in source; assert "Pessoa jurídica" in source; assert "formatDate(step.occurred_at,{time:false})" in source

def test_frontend_clears_previous_result_before_search_and_on_new_search()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "result.hidden=true" in source; assert "newSearchButton.addEventListener" in source; assert "result.hidden=true;setStatus();authPanel.hidden" not in source

def test_frontend_uses_public_api_shape_for_secret_and_unauthorized_states()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8"); assert "response.status===404" in source; assert "data.parties" in source; assert "data.subjects" in source; assert "data.recent_steps" in source; assert "innerHTML" not in source; assert "request_id" not in source


def test_frontend_summary_renderer_is_allowlisted_and_non_executable()->None:
 source=(FRONTEND/"app.js").read_text(encoding="utf-8")
 stylesheet=(FRONTEND/"app.css").read_text(encoding="utf-8")
 assert "summary-process-header" in source
 assert "summary-party" in source
 assert "data-component','ProcessHeader'" in source
 assert "data-component','Party'" in source
 assert "innerHTML" not in source
 assert "eval(" not in source
 assert "new Function" not in source
 assert "ProcessHeader\\s+className=" in source
 assert "<Party\\s+name=" in source
 assert ".summary-process-header" in stylesheet
 assert ".summary-party" in stylesheet


def test_successful_lookup_has_programmatic_result_focus_target()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8"); source=(FRONTEND/"app.js").read_text(encoding="utf-8")
 assert 'id="result-code" tabindex="-1"' in html
 assert "document.querySelector('#result-code').focus({preventScroll:true})" in source


def test_browser_security_headers_apply_to_frontend_api_and_errors()->None:
 client=TestClient(app)
 expected={
  "content-security-policy":"default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; frame-src 'none'; form-action 'self'",
  "x-content-type-options":"nosniff",
  "x-frame-options":"DENY",
  "x-xss-protection":"0",
  "referrer-policy":"no-referrer",
  "permissions-policy":"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  "cross-origin-opener-policy":"same-origin",
  "x-permitted-cross-domain-policies":"none",
 }
 for path in ("/","/app.js","/health","/missing-route"):
  response=client.get(path)
  for name,value in expected.items():
   assert response.headers[name]==value


def test_csp_matches_self_hosted_frontend_without_inline_exceptions()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8")
 assert '<script src="config.js" defer></script>' in html
 assert '<script src="app.js" defer></script>' in html
 assert '<link rel="stylesheet" href="app.css">' in html
 assert "<script>" not in html
 assert "style=" not in html
 client=TestClient(app)
 csp=client.get("/").headers["content-security-policy"]
 assert "'unsafe-inline'" not in csp
 assert "'unsafe-eval'" not in csp
 assert "frame-ancestors 'none'" in csp
 assert "object-src 'none'" in csp


def test_desktop_daily_launcher_does_not_rebuild_or_migrate()->None:
 root=Path(__file__).parents[1]
 script=(root/"scripts"/"start-ragv-desktop.ps1").read_text(encoding="utf-8-sig")
 checks=unittest.TestCase()
 checks.assertIn("docker compose up -d --no-build",script)
 checks.assertNotIn("docker compose build",script)
 checks.assertIn("update-ragv-desktop.ps1",script)
 checks.assertNotIn("docker compose down",script)
 checks.assertNotIn("volume rm",script)
 checks.assertNotIn("docker volume",script)


def test_explicit_desktop_update_builds_coordinated_services_after_backup()->None:
 root=Path(__file__).parents[1]
 script=(root/"scripts"/"update-ragv-desktop.ps1").read_text(encoding="utf-8-sig")
 backup=script.index("scripts/backup_database.sh")
 build=script.index("docker compose build migrate api worker-1 worker-2 scheduler")
 up=script.index("docker compose up -d --no-build migrate api worker-1 worker-2 scheduler")
 checks=unittest.TestCase()
 checks.assertLess(backup,build)
 checks.assertLess(build,up)
 checks.assertIn("backupFullPath",script)


def test_frontend_supports_project_pages_and_configurable_api_origin()->None:
 html=(FRONTEND/"index.html").read_text(encoding="utf-8")
 source=(FRONTEND/"app.js").read_text(encoding="utf-8")
 config=(FRONTEND/"config.js").read_text(encoding="utf-8")
 assert 'href="./"' in html
 assert 'href="app.css"' in html
 assert 'src="config.js"' in html
 assert 'src="app.js"' in html
 assert "window.RPY_PUBLIC_CONFIG" in config
 assert "apiUrl(" in source
 assert "API_NOT_CONFIGURED" in source
 checks=unittest.TestCase()
 checks.assertIn("function relativeElapsed",source)
 checks.assertIn('id="api-connectivity-label"',html)
 checks.assertEqual(html.count('class="provider-info"'),2)
 checks.assertIn('id="history-trigger"',html)
 checks.assertIn('id="confirm-modal"',html)
 assert "localStorage" not in source
 assert "sessionStorage" not in source
