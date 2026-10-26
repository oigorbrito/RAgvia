# RAgvia — LIVE-01: aceitação operacional após integração das PRs #20–#22

## Objetivo e fronteiras

Executar **uma vez** o `app.rag.generate_summary` real sobre **processo já
adquirido, finalizado, não sigiloso e vinculado ao tenant**, com autorização
financeira prévia do mesmo processo/versão/tenant. Conferir o resumo persistido
no PostgreSQL com a resposta autenticada `GET /v1/processos/{CNJ}/resumo`
em `format=jsx` e `format=json`. O script não publica texto, CNJ, segredo,
token nem payload em stdout; somente SHA-256, contagens, modelo, uso e tempo.

Este bloco **não cria novo pedido Judit**, não reativa DataJud, não executa
o doador e não substitui a validação por especialistas. Não se deve reportar
qualidade jurídica aprovada a partir de uma única geração ou da checagem
estrutural de fontes.

## Sequência operacional, sem chamadas duplicadas

1. Judit: executar o modo existente `diagnose` de
   `.github/workflows/provider-live-smoke.yml` para provar conectividade
   **sem POST**. Se autorizada uma aquisição, escolher `judit-roundtrip`
   com um CNJ público autorizado e os secrets operacionais existentes.
   O workflow Judit não persiste automaticamente o processo na aplicação.
2. Ingestão: entregar o callback legítimo ao endpoint webhook da instalação
   isolada, ou importar por `scripts/replay_judit_webhooks.py` a captura
   privada **já obtida**. Conferir correlação durável de
   `process_versions.source_request_id`, finalização e `tenant_processes`.
   Não repetir o POST Judit para tentar corrigir a ingestão.
3. Claude: habilitar **somente no worker/ambiente isolado** os controles
   de `app/generation_authorization.py`: `ANTHROPIC_PAID_ENABLED=true`,
   `PROVIDER_MAX_ATTEMPTS=1`,
   `ANTHROPIC_MODEL=claude-sonnet-4-6`,
   `ANTHROPIC_APPROVAL_FILE` com autorização específica, vigente e
   evidências de conta/limite **realmente verificadas**, `DATABASE_URL`
   com schema 035 e `ANTHROPIC_API_KEY`. Cota reservada máxima desta
   versão: USD 6.06 sem regeneração; USD 12.12 com uma regeneração.
   São reservas pessimistas, não custo medido. Não utilizar o script
   adversarial de múltiplos casos como substituto desse controle.
4. Preflight **sem gasto**:

   ```powershell
   python -m scripts.accept_live_summary --process-id <UUID_PROCESSO> --version-id <UUID_VERSAO> --tenant-id <UUID_TENANT>
   ```

   Só `CONFIGURATION_VALID_NOT_LIVE_ACCEPTED` autoriza ir ao próximo passo.
   Isso verifica declaração de autorização e estado no banco, **não**
   comprova direitos, saldo Anthropic, contrato Judit ou limite externo;
   esses comprovantes são responsabilidade operacional prévia.
5. Aceitação real controlada (apenas com API HTTPS ou loopback já em operação
   e token autenticado na variável `RAGVIA_ACCEPTANCE_API_TOKEN`).
   Para APIs HTTPS remotas, também fixar exatamente a origem permitida em
   `RAGVIA_ACCEPTANCE_API_ORIGIN`; em loopback não é necessária:

   ```powershell
   python -m scripts.accept_live_summary --process-id <UUID_PROCESSO> --version-id <UUID_VERSAO> --tenant-id <UUID_TENANT> --api-base-url https://api-exemplo-autorizada --execute
   ```

   Este comando é **one shot**. Nunca executar novamente automaticamente
   após erro, timeout ou resposta perdida: uma reserva pode já ter sido
   consumida. Conferir o ledger e o billing Anthropic antes de qualquer
   autorização nova.
6. Registrar somente o JSON de metadados, HEAD, hash do resumo, resultado
   do ledger e os quatro critérios de revisão humana cega. O valor
   `provider_reported_cost_usd=null` significa *não disponibilizado pelo
   provedor neste caminho*, nunca custo zero. Verificar custo real no billing.
   O HTML/browser/WebView deve ser validado separadamente sob o mesmo
   ambiente autorizado; hash de API não comprova renderização visual.

## Limites de segurança e bloqueios

- Sem credenciais, banco, permissão tenant, versão finalizada, aprovação
  financeira, API autenticada ou HTTPS/loopback: `BLOCKED`, zero chamadas
  intencionalmente efetuadas pelo executor.
- Para execução real, o script também recusa um endpoint remoto não fixado
  explicitamente e evita DataJud, anexos pagos Judit, embeddings e reranking
  externos adicionais. Use somente o serviço de API controlado pelo operador.
- Se resumo já publicado ou `approval_id` usado: **não** gera de novo.
- As reservas são duráveis e não são revertidas após falhas. Dados
  processuais e credenciais não entram em logs/artefatos CI.
- `LIVE_GENERATION_AND_API_CONTRACT_CHECKED` atesta transporte e paridade
  técnica daquele caso; **não** atesta acurácia jurídica ou implantação.
- Nunca usar processo sigiloso como teste, nem publicar IDs pessoais de
  processos reais no GitHub Actions. O runbook de Judit continua vigente.
- O workflow `adversarial-live.yml` não dispõe hoje de escopo por caso
  para seu próprio controle de gastos. Não o usar com chave real: precisa
  de autorização separada para cada caso, não bypass do guard.

## Estado deste bloco

Esta PR instrumenta o percurso real, mas os testes de CI usam apenas
fixtures/mocks **sem tráfego externo**. A aceitação real exige execução
no ambiente autorizado e só será considerada comprovada mediante
metadados de uma execução legítima, verificação do billing e revisão
jurídica humana.
