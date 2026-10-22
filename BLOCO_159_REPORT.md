# RagV — Relatório de Validação Operacional do Desktop Windows (Bloco 159)

## 1. Identificação e Ambiente

- **Repositório:** `<repo-root>`
- **Branch:** `chore/staging-validation-158`
- **HEAD base:** `2d4454ba851c54ee9294093996291b225494f67f`
- **Sistema Operacional:** Windows 11 Pro 64-bit
- **Python:** 3.12.10 (ambiente isolado `.venv-desktop` com `pythonnet 3.2.1` e `pywebview 6.2.1`)
- **WebView2 Runtime:** Microsoft Edge WebView2 v155.0.42 (Edge Chromium)
- **Compilador PyInstaller:** 6.22.3
- **Compilador Inno Setup:** Inno Setup 6.7.3 (`ISCC.exe`)
- **Backend/DB descartável:** PostgreSQL 16 + pgvector em container descartável (`ragv-staging-postgres`, porta 5435) com API FastAPI na porta 8008

---

## 2. Versões das Ferramentas de Build

| Ferramenta | Versão | Localização |
|---|---|---|
| Python (Ambiente Desktop) | 3.12.10 | `<repo-root>` |
| PyInstaller | 6.22.3 | `<repo-root>` |
| pywebview | 6.2.1 | Biblioteca Python para renderização WebView2 nativa |
| pythonnet | 3.2.1 | Bridge .NET / WinForms para integração do WebView2 |
| Inno Setup Compiler | 6.7.3 | `<user-profile> Setup 6\ISCC.exe` |

---

## 3. Resultado da Execução da Janela Nativa (PyWebView)

| Item Verificado | Resultado | Detalhe da Evidência |
|---|---|---|
| 1. Abertura de janela nativa PyWebView | **PASS** | Janela criada via `webview.create_window` com backend EdgeChromium / WebView2 ativo. |
| 2. Carregamento correto da interface | **PASS** | HTML, CSS e JavaScript do frontend carregados sem erros de console ou falhas de layout. |
| 3. Ausência de janela PowerShell extra | **PASS** | Execução via `start-ragv-hidden.vbs` e `start-installed-hidden.vbs` utiliza `SW_HIDE` (`0, False`). O binário compilado `RagVDesktop.exe` usa `console=False` (bootloader `runw.exe`), garantindo ausência de janela de console associada. |
| 4. Tratamento de API indisponível | **PASS** | Quando a API não responde em `/ready`, o launcher aciona `show_error` via Win32 `MessageBoxW` ("RagV indisponivel. Verifique o Docker e a API.") e encerra com código de saída 1. Testado no script e no executável compilado. |
| 5. Fechamento sem processos órfãos | **PASS** | Após o encerramento da janela nativa, nenhum processo residual de `RagVDesktop.exe` ou subprocessos python permanece ativo. |
| 6. Ausência de chamadas pagas externas | **PASS** | Execução isolada em ambiente de testes; nenhuma requisição direcionada a provedores externos (Judit/OpenAI/Anthropic). |

---

## 4. Resultado do Fluxo Integrado Desktop

Executado na janela nativa PyWebView conectada à API e banco PostgreSQL descartáveis:

| Cenário / Passo | Status | Detalhe Observado |
|---|---|---|
| Judit selecionada por padrão | **PASS** | Elemento `#source-judit` com atributo `checked = true`. |
| Indicador de acesso neutro inicial | **PASS** | Label "Configurar acesso", classe CSS neutra `access-trigger`. |
| Rejeição de credencial inválida | **PASS** | Token inválido recusado pelo backend, classe `access-trigger invalid`, mensagem de erro exibida. |
| Autenticação real via backend | **PASS** | Token válido autenticado no endpoint `/auth/verify`, classe `authenticated configured`, label "Acesso autenticado". |
| Consulta a processo já armazenado | **PASS** | CNJ `0000000-00.2026.8.21.0001` detectado com histórico existente; exibe "Processo já consultado". |
| Abertura de resumo existente | **PASS** | Clique em "Abrir histórico existente" renderiza o resumo armazenado localmente sem requisições à Judit. |
| Aviso de possível cobrança | **PASS** | Clique em "Consultar novamente na Judit" abre modal `#confirm-modal` alertando sobre cobrança adicional. |
| Cancelamento de nova aquisição | **PASS** | Clique em "Cancelar" oculta o modal (`hidden = true`) sem emitir requisições ou autorizações. |
| Confirmação de aquisição simulada | **PASS** | Confirmação gera token de uso único na tabela `acquisition_authorizations`, enfileira job na tabela `jobs` e arquiva o registro anterior em `tenant_judit_request_history`. Status atualizado para "Consulta registrada". |
| Bloqueio de falha ambígua | **PASS** | CNJ `0000003-00.2026.8.21.0001` em estado `failed_ambiguous` bloqueia nova consulta automática e exibe aviso de incerteza operacional. |
| DataJud desabilitado | **PASS** | Opção `#source-datajud` desabilitada com aviso de conformidade (`DATAJUD_ENABLED=false`). |

---

## 5. Resultado de Build, Instalação e Desinstalação

### A. Build Local
- **PyInstaller:** Gerou `dist\RagVDesktop.exe` (11.8 MB) a partir do spec `RagVDesktop.spec`.
- **Inno Setup:** Compilou `dist-installer\RagVDesktop-Setup-0.1.0.exe` (13.6 MB) via `ISCC.exe` em 1.8 segundos.

### B. Instalação em Ambiente Isolado
- Diretório de teste: `<user-profile>`
- Execução silenciosa: `RagVDesktop-Setup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="..." /NORESTART` (Exit code: 0)
- Arquivos instalados verificados:
  - `RagVDesktop.exe`
  - `start-installed.ps1`
  - `start-installed-hidden.vbs`
  - `unins000.exe` / `unins000.dat`

### C. Atalho no Menu Iniciar
- Atalho criado: `<user-profile> Menu\Programs\RagV Desktop.lnk`
- TargetPath: `<windows-root>`
- Arguments: `"<user-profile>`
- WorkingDirectory: `<user-profile>`

### D. Execução e Independência do Repositório
- O executável instalado foi iniciado diretamente a partir de `C:\` sem depender de caminhos do repositório ou de interpretador Python do sistema, conectando-se normalmente à API configurada.
- Script `start-installed.ps1` valida existência de `compose.yaml`, aguarda prontidão da API em `/ready` e dispara o executável nativo.

### E. Desinstalação Completa
- Execução: `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` (Exit code: 0)
- Verificação pós-desinstalação:
  - Diretório de teste: Removido integralmente (`Test-Path: False`).
  - Atalho no Menu Iniciar: Removido integralmente (`Test-Path: False`).

---

## 6. Defeitos Encontrados e Correções Aplicadas

1. **Configuração flexível de URL para ambientes de teste e staging:**
   - *Problema:* `scripts/ragv_desktop.py` fixava a URL exclusivamente como `"http://127.0.0.1:8000"`, o que inviabilizava testes locais isolados contra portas alternativas de staging (como 8008) sem alterar o arquivo.
   - *Correção:* Implementado o helper `get_url()` com leitura da variável de ambiente `RPY_DESKTOP_URL` e fallback para `"http://127.0.0.1:8000"`.
   - *Garantia:* Total compatibilidade com a topologia padrão sem alterar contratos ou exigir configurações adicionais em produção.
2. **Cobertura de teste unitário:**
   - Adicionado teste `test_main_custom_url_from_env` em `tests/test_ragv_desktop.py`, validando a leitura correta de `RPY_DESKTOP_URL`.

---

## 7. Verificações de Qualidade e Conformidade

- **Unit tests desktop:** `tests/test_ragv_desktop.py` (8/8 testes PASS).
- **Frontend behavior harness:** `node tests/frontend_behavior_test.mjs` (38/38 asserções PASS).
- **Ruff linter:** `ruff check scripts/ragv_desktop.py tests/test_ragv_desktop.py` (All checks passed).
- **Project Harness:** `python scripts/project_harness.py` (26/26 guardrails e evidências PASS).

---

## 8. Arquivos Modificados / Incluídos

- `scripts/ragv_desktop.py` (suporte a `RPY_DESKTOP_URL` via `get_url()`)
- `tests/test_ragv_desktop.py` (novo caso de teste para URL customizada)
- `RagVDesktop.spec` (especificação PyInstaller versionada)
- `BLOCO_159_REPORT.md` (relatório oficial de evidências da validação)

---

## 9. Decisão Final

**PASS**

O aplicativo RagV Desktop no Windows cumpre integralmente todos os requisitos funcionais, operacionais, de empacotamento, instalação, execução de interface nativa e desinstalação.
