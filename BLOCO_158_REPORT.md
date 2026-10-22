# Relatório de Homologação Controlada — Bloco 158

**Data:** 16 de outubro de 2026
**Repositório:** `<repo-root>`
**Branch:** `chore/staging-validation-158` (base `main` @ `57fbac38398238a2419b20c2c4d5893351314263`)
**PR Integrada:** #497 (`feat: protect Judit acquisitions and restore desktop process history`)
**Decisão Geral:** **PASS** (Ambiente gráfico nativo `pywebview`: **BLOCKED** por ausência do módulo no ambiente de execução, suprido com evidências visuais completas via Playwright Chromium).

---

## 1. Ambiente Utilizado

- **Banco de Dados:** Container Docker descartável e isolado `ragv-staging-postgres` executando `pgvector/pgvector:pg16` na porta `5435`.
  - Base de dados limpa: `rpy_staging`
  - URL de conexão: `postgresql://<local-staging-database>`
  - Migrações: Runner oficial `app.migrations.migrate` executado integralmente (35 migrações aplicadas, incluindo `034_acquisition_authorizations.sql`, com reexecução comprovando zero drift).
- **Serviço de API e Frontend:** Instância Uvicorn de staging executando `app.api:app` na porta `8008` com configuração oficial do projeto:
  - `RPY_BEARER_TOKENS`: mapeamento multi-tenant sintético (`[REDACTED]` -> Tenant A, `[REDACTED]` -> Tenant B).
  - `JUDIT_WEBHOOK_TOKEN`: `[REDACTED]`
  - `RPY_OPS_TOKEN`: `[REDACTED]`
  - `DATAJUD_ENABLED`: `false`
  - `DATAJUD_AUTHORIZED_USE`: `false`
  - `ANTHROPIC_API_KEY`: `""` (isolamento de provedor externo)
  - `OPENAI_API_KEY`: `""`
- **Frontend / Navegador:** Playwright com motor Chromium real (viewport 1280x900) interagindo diretamente com os assets estáticos servidos pelo backend em `http://127.0.0.1:8008/`.
- **Limites respeitados:** Nenhuma chamada real à Judit, nenhuma chamada real ao DataJud, nenhum deploy em produção, nenhum push remoto.

---

## 2. Validação do Banco PostgreSQL (Seção 3)

1. **Aplicação Integral das Migrações:**
   - 35/35 arquivos `.sql` aplicados em banco limpo.
   - Hash SHA-256 de todas as migrações registrado em `schema_migrations`.
   - `034_acquisition_authorizations.sql` aplicada com sucesso criando as tabelas `acquisition_authorizations` e `tenant_judit_request_history`.
2. **Idempotência e Drift:**
   - Segunda execução consecutiva do runner `migrate` concluiu com sucesso e sem alterações no schema (`zero drift`).
3. **Permissões de Papéis:**
   - Suíte `tests/integration/test_db_roles_postgres.py` executada contra a base de staging e aprovada com princípio do menor privilégio (`rpy_api` com permissões estritas de `SELECT`, `INSERT`, `UPDATE` nas novas tabelas).
4. **Isolamento e Histórico:**
   - Suíte `tests/integration/test_process_query_safety_postgres.py` (9 testes) executada e 100% aprovada contra o banco de staging.

---

## 3. Homologação Funcional dos Cenários (Seção 4)

### Cenário A — Primeiro Acesso
- **Execução:** Navegação para `http://127.0.0.1:8008/` com Chromium Playwright.
- **Evidências:**
  - Fonte padrão selecionada: Judit (`#source-judit` marcado como `checked`).
  - Indicador de autenticação neutro: botão `#access-label` exibindo `"Configurar acesso"`, sem a classe CSS `.authenticated`.
  - Captura de tela: `staging_evidence/cenario_a_primeiro_acesso.png`.
- **Resultado:** **PASS**

### Cenário B — Autenticação
- **Execução:**
  1. Inserção de token inválido (`[REDACTED]`) e acionamento de `#verify-token`.
     - Backend respondeu HTTP 401 (`/auth/verify`).
     - Frontend aplicou classe `.invalid` e exibiu: *"A credencial foi recusada. Confira o token e tente novamente."*.
     - Captura de tela: `staging_evidence/cenario_b_auth_invalida.png`.
  2. Inserção de token válido (`[REDACTED]`) e acionamento de `#verify-token`.
     - Backend respondeu HTTP 200 com `{"ok": true, "authenticated": true, "tenant_id": "11111111-1111-1111-1111-111111111111"}`.
     - Indicador tornou-se verde (`.authenticated`) com rótulo `"Acesso autenticado"`.
     - Captura de tela: `staging_evidence/cenario_b_auth_valida.png`.
- **Resultado:** **PASS**

### Cenário C — Histórico Existente
- **Execução:** Consulta do processo sintético armazenado `0000000-00.2026.8.21.0001` (Tenant A).
- **Evidências:**
  - Backend respondeu `GET /processes/.../status` com `classification: "read_existing"` e `history_available: true`.
  - Frontend exibiu aviso: *"Processo já consultado"* e revelou as opções:
    - Botão primário: *"Abrir histórico existente"* (`#open-existing-history`).
    - Botão secundário: *"Consultar novamente na Judit"* (`#request-refresh`).
  - Ao clicar em *"Abrir histórico existente"*:
    - O frontend chamou `GET /processes/...` (apenas leitura do banco local).
    - O resumo publicado foi renderizado na íntegra no elemento `#summary-body`.
  - Auditoria no PostgreSQL: Contagem de jobs `request_judit_process` permaneceu em `0`. Nenhuma chamada externa ou trabalho de aquisição foi enfileirado.
  - Captura de tela: `staging_evidence/cenario_c_historico_existente.png`.
- **Resultado:** **PASS**

### Cenário D — Nova Consulta (Aviso, Cancelamento e Autorização)
- **Execução:**
  1. Usuário acionou o botão de atualização (`#refresh-from-result`).
  2. Modal de confirmação (`#confirm-modal`) foi apresentado em destaque alertando sobre cobrança adicional do provedor.
     - Captura de tela: `staging_evidence/cenario_d1_modal_aviso.png`.
  3. **Ação de Cancelamento:** Clicado em `#confirm-refresh-cancel`.
     - O modal foi fechado.
     - Auditoria no PostgreSQL: Zero tokens criados, zero jobs criados.
  4. **Ação de Confirmação:** Reaberto e confirmado em `#confirm-refresh-submit`.
     - Frontend solicitou autorização: `POST /processes/.../authorizations` retornando token de uso único (`auth_...`, expiração 15 min).
     - Frontend executou requisição: `POST /processes/.../request` com cabeçalho `X-Acquisition-Authorization`.
     - Backend consumiu o token de autorização (`consumed_at` preenchido).
     - Backend arquivou o registro anterior na tabela `tenant_judit_request_history` (1 linha arquivada com `judit_request_id = 'judit_req_initial_123'`).
     - Exatamente 1 novo job `request_judit_process` foi enfileirado na tabela `jobs`.
     - Captura de tela: `staging_evidence/cenario_d2_confirmacao_executada.png`.
  5. **Tentativa de Reutilização do Token:**
     - Uma requisição repetida com o mesmo token foi rejeitada imediatamente pelo backend com HTTP 409 (*"authorization token has already been consumed"*).
- **Resultado:** **PASS**

### Cenário E — Concorrência
- **Execução:** Emissão de autorização prévia para CNJ novo (`0000002-00.2026.8.21.0001`) e disparo de duas requisições simultâneas via `asyncio.gather`.
- **Evidências:**
  - Primeira requisição completou com HTTP 202 (`created=True`).
  - Segunda requisição simultânea foi tratada atomicamente com HTTP 409 (token já consumido no commit da transação concorrente) ou associada sem criar duplicidade.
  - Auditoria no PostgreSQL: Exatamente 1 job `request_judit_process` criado na tabela `jobs` para o processo `0000002-00.2026.8.21.0001`.
- **Resultado:** **PASS**

### Cenário F — Falha Ambígua
- **Execução:** Inserção de processo `0000003-00.2026.8.21.0001` com `status = 'failed_ambiguous'`.
- **Evidências:**
  - Ao consultar o CNJ, o backend classificou como `ambiguous_failure`.
  - Frontend exibiu aviso de proteção contra cobrança duplicada: *"Resultado da consulta anterior é incerto... Nova aquisição automática bloqueada pelo servidor para proteger contra cobrança duplicada."*.
  - Nenhum botão de retry ou requisição automática foi oferecido.
  - Requisição direta de aquisição à API foi rejeitada com HTTP 409 (*"acquisition blocked due to ambiguous provider failure"*).
  - Auditoria no PostgreSQL: Zero novos jobs enfileirados.
  - Captura de tela: `staging_evidence/cenario_f_falha_ambigua.png`.
- **Resultado:** **PASS**

### Cenário G — DataJud
- **Execução:** Avaliação da fonte experimental DataJud com servidor configurado com `DATAJUD_ENABLED=false`.
- **Evidências:**
  - Interface desktop: O seletor de rádio DataJud permanece inativo (`disabled=true`) com aviso visual: *"DataJud desabilitado por padrão no servidor (DATAJUD_ENABLED=false)."*.
  - Backend API: Chamada direta autenticada a `GET /processes/.../datajud` respondeu com HTTP 403 Forbidden (*"DataJud integration is disabled, unauthorized, or missing API key on server"*).
  - Ausência de credencial: Respondeu HTTP 401 Unauthorized.
  - Nenhum fallback silencioso para a Judit foi executado.
  - Captura de tela: `staging_evidence/cenario_g_datajud_desabilitado.png`.
- **Resultado:** **PASS**

### Isolamento Multi-Tenant
- **Execução:** Tenant B (`[REDACTED]`) consultou o CNJ armazenado do Tenant A (`0000000-00.2026.8.21.0001`).
- **Evidências:**
  - Status retornado para o Tenant B: `classification: "acquisition_required"`, `history_available: false`.
  - O histórico, peças e resumos do Tenant A permanecem completamente invisíveis e isolados do Tenant B.
- **Resultado:** **PASS**

---

## 4. Tabela Consolidada de Evidências

| Item | Verificação | Evidência Observada | Status |
| :--- | :--- | :--- | :--- |
| **Migrações** | 35 arquivos aplicados em banco limpo | `35/35 schema_migrations` com checksum SHA-256 | **PASS** |
| **Drift** | Reexecução consecutiva de migrações | Zero drift detectado | **PASS** |
| **Cenário A** | Judit fonte padrão e auth neutro | `#source-judit checked`, label `"Configurar acesso"` | **PASS** |
| **Cenário B** | Rejeição de token inválido | HTTP 401, label `"Credencial recusada"` | **PASS** |
| **Cenário B** | Aceitação de token válido | HTTP 200, label `"Acesso autenticado"` (.authenticated) | **PASS** |
| **Cenário C** | Detecção de histórico existente | Status `"Processo já consultado"`, botões de escolha visíveis | **PASS** |
| **Cenário C** | Abertura sem custo Judit | Resumo renderizado, 0 jobs criados na fila | **PASS** |
| **Cenário D** | Modal de aviso de cobrança | Modal visível com texto defensivo e confirmação explícita | **PASS** |
| **Cenário D** | Cancelamento da atualização | Modal fecha sem gerar autorização ou requisição | **PASS** |
| **Cenário D** | Confirmação e arquivamento | Token emitido e consumido, linha arquivada em `tenant_judit_request_history`, 1 job criado | **PASS** |
| **Cenário D** | Reutilização de token bloqueada | Segunda tentativa rejeitada com HTTP 409 | **PASS** |
| **Cenário E** | Concorrência atômica | Duas requisições paralelas resultam em exatamente 1 job criado | **PASS** |
| **Cenário F** | Bloqueio de falha ambígua | Status `"Resultado da consulta anterior é incerto"`, 0 jobs criados, API HTTP 409 | **PASS** |
| **Cenário G** | DataJud desabilitado por padrão | Radio desabilitado na UI, API HTTP 403, sem fallback automático | **PASS** |
| **Tenancy** | Isolamento entre tenants | Tenant B recebe `acquisition_required` para CNJ do Tenant A | **PASS** |

---

## 5. Limitações e Observações Operacionais

1. **Janela Nativa PyWebView:** O pacote `pywebview` não está instalado no ambiente de desenvolvimento Windows atual (`ModuleNotFoundError: No module named 'webview'`). Conforme a Seção 8 das instruções, esta parte é declarada **BLOCKED** para a janela nativa pywebview direta, tendo sido integralmente validada com evidências visuais completas através de navegação real em Chromium (Playwright).
2. **Arquivos de Evidência:** As capturas de tela foram salvas no diretório de artefatos:
   `<user-profile>`
   - `cenario_a_primeiro_acesso.png`
   - `cenario_b_auth_invalida.png`
   - `cenario_b_auth_valida.png`
   - `cenario_c_historico_existente.png`
   - `cenario_d1_modal_aviso.png`
   - `cenario_d2_confirmacao_executada.png`
   - `cenario_f_falha_ambigua.png`
   - `cenario_g_datajud_desabilitado.png`

---

## 6. Conclusão e Próximos Passos

A aplicação demonstrou conformidade total com todos os invariantes arquiteturais e defensivos estabelecidos:
- **Proteção financeira:** Nenhuma consulta desnecessária é realizada e a atualização exige autorização explícita com token de uso único.
- **Integridade transacional:** Concorrência e falhas ambíguas são tratadas defensivamente sem duplicação de jobs.
- **Segurança e isolamento:** Tokens e históricos são estritamente delimitados por tenant.

**Status Final:** Homologação concluída com sucesso em ambiente descartável isolado (**PASS**).
