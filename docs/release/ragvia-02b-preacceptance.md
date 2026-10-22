# RAGVIA-02B — pré-aceitação offline, não autorização de gasto

Base: PR #11, `1e468df45d45d7e68fcad11648cb307cfec77652`.
Branch: `work/ragvia-02b-preacceptance`. A candidata 01D, a PR #11 e o RagV
original permanecem intocados. Nenhuma configuração persistente foi substituída.
Contratos afetados: `GENERATION-SPEND-001`, `API-FASTAPI-001`, `TENANT-001`,
`QUEUE-WORKER-001`, `HISTORY-001` e `ENV-ISOLATION-001`.

## Modelo e evidência pública (consulta em 2026-10-22)

**Proposta explícita: `claude-sonnet-4-6`**, somente para futura aceitação
controlada. É um modelo documentado como **legacy**, não uma recomendação de
qualidade ou do modelo mais recente. Não trocar automaticamente por outro ID.
A linha atual documentada inclui `claude-fable-5-1`, `claude-opus-5-5`,
`claude-sonnet-5-5`, `claude-haiku-5-5`. A página de preços também lista Sonnet 5;
o nome interno `claude-sonnet-5` nunca foi evidência de habilitação da conta.
Defaults de roteamento antigos foram preservados, mas não autorizam gasto.

| Questão | Evidência / situação |
|---|---|
| ID proposto publicamente documentado | [Sonnet 4.6](https://platform.claude.com/docs/en/models/sonnet-4-6/overview): ID `claude-sonnet-4-6`, contexto 1M, saída máxima 128K tokens |
| Preço público padrão global | [Pricing](https://platform.claude.com/docs/en/about-claude/pricing): USD 3/MTok entrada, 15 saída, 3.75 cache-write 5min, 6 cache-write 1h, 0.30 cache-read |
| Structured Outputs | [Contrato oficial](https://platform.claude.com/docs/en/build-with-claude/structured-outputs): Sonnet 4.6 listado; `output_config.format` / `json_schema`; objetos fechados |
| Compatibilidade técnica do adaptador | Schema de wire ajustado pelo `anthropic.transform_schema` do SDK existente; parsing, limites e validação factual locais preservados; verificação sintética, não compatibilidade live comprovada |
| Conta habilitada | **PENDENTE**: nenhuma chave foi usada nem Models API consultada. Requer evidência específica da conta/ID, por responsável autorizado |
| SDK e retries | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python): default 2 retries para conexão, 408/409/429/5xx; default timeout 10min. RagV mantém SDK `max_retries=0` e timeout explícito; a nova política exige `PROVIDER_MAX_ATTEMPTS=1` |
| Controle externo | [Spend limits](https://platform.claude.com/docs/en/api/rate-limits): limites de organização/workspace; workspace default não admite limite customizado; rate limit não é teto financeiro |

URLs finais, datas e SHA-256 das páginas consultadas estão em
[evidência de fontes oficiais](evidence/ragvia-02b-official-sources.json).
Foram GETs públicos sem credenciais, não chamadas de geração. A consulta inicial
`.md` falhou e uma URL de modelo retornou apenas shell HTML; só as páginas com
conteúdo efetivo foram usadas. SDK pinado: `anthropic==1.11.0` na imagem Linux.
O Python Windows deste host tem `anthropic==0.125.0`; seus resultados não
substituem a execução direcionada com o SDK pinado.

### Correção concreta do schema

O schema da PR #11 enviava `maxLength`, `maxItems` e `uniqueItems` diretamente.
A documentação oficial informa que essas restrições não são aceitas no wire
(podem resultar em HTTP 400). Agora `_generate` usa o transformador do SDK,
sem outro cliente/pipeline. O schema local original não é mutado: os limites
continuam no parser/validador. Todos os campos obrigatórios e
`additionalProperties=false` continuam no wire. Refusal e `max_tokens` continuam
bloqueando publicação; nunca aumentar tokens automaticamente para tentar passar.

## Matriz financeira e limites

| Controle | Aplicação / limitação |
|---|---|
| Default sem gasto | `ANTHROPIC_PAID_ENABLED=false` (ou ausente), mesmo com chave: bloqueia antes de construir SDK |
| Aprovação privada | `ANTHROPIC_APPROVAL_FILE`, JSON estrito, <=8KB; não aceito por endpoint público. Requer ID, referências de autorização dos dados, conta e limite externo |
| Escopo | Aprovação vincula tenant, processo e versão exatos; chamadas sem escopo são bloqueadas. Antes de cada mensagem, DB confirma carteira, versão corrente e sigilo zero |
| Modelo | `ANTHROPIC_MODEL` deve ser explícito e igual ao aprovado; somente `claude-sonnet-4-6` tem perfil de preço revisado nesta versão. Outros IDs bloqueiam mesmo se públicos |
| Validade | UTC com timezone, expiração futura em até 24h; verificação também pelo relógio do PostgreSQL antes da reserva |
| Orçamento | Valores monetários em strings decimais USD, finitos/positivos. `estimativa <= limite externo declarado <= aprovado` |
| Estimativa conservadora | Por mensagem: contexto inteiro 1M a USD 6/MTok (cache-write mais caro, não cache hit presumido) + 4000 tokens a USD 15/MTok = USD 6.06. Com uma regeneração: **USD 12.12 reservados**. Não é previsão de consumo típico |
| Tentativas | SDK zero retries; wrapper local exige uma tentativa. Manifesto permite zero/uma regeneração; máximo uma/duas mensagens, incluindo resposta eventualmente perdida |
| Reserva durável | Migração 035: aprovação single-use, custo integral reservado antes da rede, sem refund automático. PK arbitra concorrência; novo cliente/worker/queue retry não reutiliza a aprovação. O mesmo cliente só incrementa os slots autorizados |
| Interrupção | Documento ausente/inválido, valor insuficiente, falta de referência, outro modelo, escopo perdido, DB indisponível, expiração, limite consumido ou campos de request adicionais: falha permanente, sem rede |
| Timeout | Continua configurável e menor que o timeout do worker. Proposta futura: 45s/request, worker 120s. Timeout não desfaz reserva e pode ter cobrança sem resposta/usage |
| Superfície de custo | Apenas request canônico de texto com schema, sem tools, mídia, fast tier, headers extras ou seletor geográfico. Sem batch ou descontos presumidos |
| Limite externo real | **Obrigatório para autorizar**: operador verifica controle efetivo, budget restante e isolamento da chave/workspace. Referência + booleano no manifesto são declarações de configuração confiável, não prova automática da existência/eficácia do limite |

**Não há promessa de teto monetário absoluto.** A estimativa depende dos preços
publicados, contexto documentado e superfície do request; taxas, câmbio,
mudanças de preço, outros consumidores e comportamento de billing ficam fora
dessa conta. A app não consulta nem configura billing. Na ausência de garantia
externa suficiente/revisão das evidências, permanecer desabilitado. Não usar
saldo/rate-limit/contabilização posterior como garantia de teto. A evidência da
conta e do limite real desta instalação continua **PENDENTE**.

O manifesto exige: `approval_id`, `model`, `process_id`, `version_id`,
`tenant_id`, `data_authorization_ref`, `account_evidence_ref`,
`external_limit_ref`, `external_limit_verified=true`, `currency=USD`,
`approved_usd`, `external_limit_usd`, `max_provider_attempts=1`,
`max_regenerations` (0/1), `expires_at`. Referências devem ter formato
`[A-Z][A-Z0-9-]{5,95}`. O arquivo é provisionado pelo operador/revisor confiável,
fora do Git, em mount read-only restrito ao worker. A app valida formato e
coerência, **não cria nem autentica autorização documental**. Não reutilizar IDs
ou editar um documento já consumido. Nenhum manifesto real foi criado aqui.

## Entradas e demonstração

**A — sintético:** fixture e documento da PR #11 preservados:
[JSON Judit sintético](../../tests/fixtures/judit/ragvia_02a_synthetic.json) e
[Markdown legível](examples/ragvia-02a-synthetic-summary.md). Sem CPF/CNPJ,
endereços, documentos privados ou identificadores judiciais reais.
O cliente simulado continua respondendo apenas na fronteira SDK. Agora o teste
usa a fábrica, aprovação **explicitamente fictícia**, reserva financeira e
parsing reais; isso não comprova habilitação da conta. Nenhum bypass de teste
foi introduzido no runtime.

**B — futuro JSON autorizado:** responsável comprova autorização do caso e do
gasto, minimiza campos antes da importação e classifica sigilo. Casos sigilosos
permanecem no resumo local e não recebem aprovação externa. No projeto Docker
novo, vincular tenant/carteira e correlacionar o request do bundle com
`tenant_judit_requests` antes de gerar. O teste inicial 02B expôs que apenas
`JUDIT_WEBHOOK_TENANT_ID` não resolve o tenant do job de geração: a política
bloqueou corretamente. O teste foi corrigido com a correlação durável, sem
relaxar autorização. Usar o replay/importador Judit existente, sem nova consulta
paga; não sobrescrever versões finalizadas nem reutilizar dados/volumes habituais.
Identificadores, payload minimizado e evidências do caso real ficam privados.

### Reprodução proporcional sem gasto

```powershell
$env:PYTHONPATH = "."
$env:RAGV_02A_ARTIFACT_DIR = "$env:TEMP/ragvia02b-new/document"
$env:RAGV_02B_LIVE_BROWSER = "true"
python scripts/validate_candidate_postgres.py --output "$env:TEMP/ragvia02b-new/postgres" --test-path tests/integration/test_generation_authorization_postgres.py --test-path tests/integration/test_legal_delivery_acceptance.py
```

Essa opção executa a interface real servida por Uvicorn em loopback efêmero,
conectada à API e ao mesmo PostgreSQL; **não há route mock/replay no browser**.
Verifica leitura autorizada, apresentação do resumo e clipboard com cronologia.
A API de rede usa o pool e estado configurados pelo teste, com lifespan desligado;
não é validação de startup/deploy nem WebView2. Os artefatos indicam se essa
perna live foi executada. Playwright/Chromium precisam estar instalados; se o
flag for solicitado e o browser faltar/falhar, o teste falha, não vira skip.

`--test-path` aceita somente arquivos/diretórios sob `tests/integration`.
O executor mantém digest PostgreSQL fixo, tmpfs, porta loopback, readiness SQL,
JUnit, rejeição de skips e limpeza do recurso próprio. A saída deve ser nova.
Além dessa perna, executar a seleção registrada em `qualification.json`: contratos
Anthropic/schema, sigilo, tenant, schema-v2/v3, evidências, publicação inválida,
roles, migração, fila/worker afetados, harness, Ruff, frontend e Gitleaks.
Resultados antigos da PR #11 qualificam só aquele SHA, não código novo.
No checkout 02B foram observados **116 unitários direcionados** na imagem Linux
com SDK 1.11.0 e **26 integrações PostgreSQL direcionadas**, sem skips, incluindo
interface HTTP live/cópia, roles, migração, sigilo e publicação inválida. O
primeiro run de integração teve 24 PASS/1 FAIL (tenant sem correlação durável);
a correção e a reprodução estão descritas acima e nos logs. Não se mudou a
expectativa de autorização para obter aprovação.
A primeira CI (`39735945207`, SHA `81655eb`) teve 169 PASS/2 FAIL na integração:
dois testes antigos compactados de resiliência mockavam `_generate`, mas ainda
construíam a fábrica de rede. O guard bloqueou antes de qualquer chamada. Os
testes agora explicitam o cliente simulado, sem alterar asserções de publicação
ou retry; o arquivo foi validado separadamente (3 testes). A aprovação final
depende da nova CI do SHA corrigido, registrada no PR/`qualification.json`.

## Migração, recuperação e rollback

A migração 035 é aditiva e compatível com leitores antigos; aplicar antes do
novo worker e reprovisionar roles. Só worker insere/lê reserva e incrementa
`messages_reserved`; não pode apagar/refundar. API/scheduler não recebem write
nem arquivo/chave de aprovação. Ledger guarda UUIDs/referências/hash/custo,
nunca texto processual, CNJ, credencial ou payload. Sem FK destrutiva: expunge
não torna uma aprovação reutilizável. Não adicionar limpeza automática sem
política aprovada para esse registro financeiro.

**Não permitir workers antigos com credenciais durante habilitação futura:**
elas não conhecem o guard. Drenar/parar versões antigas antes de habilitar.
Rollback de app pode remover a proteção; antes disso, desabilitar gasto e
revogar/remover chave. Preservar migração/ledger, sem DROP nem refunds.
Após restore, manter gasto desabilitado até reconciliar ledger com billing e
revogar aprovações potencialmente ausentes no backup. Restore não concede
novamente o direito de gastar. Não apagar ledger para fazer um retry funcionar.

## Checklist reservado para Claude real

- [ ] Autorização documental do caso/destinatário e de USD suficientes; aprovação não gerada pela app.
- [ ] Evidência da conta para `claude-sonnet-4-6`, política de dados e revisão humana das fontes/preços atuais.
- [ ] Limite externo efetivo, restante suficiente e workspace/chave isolados; se insuficiente, STOP.
- [ ] JSON minimizado, sigilo zero, tenant/carteira e request correlacionados, proveniência preservada.
- [ ] Ambiente exclusivo; migração 035/roles; imagem qualificada; nenhum worker antigo habilitado.
- [ ] Aprovação privada nova vinculada à versão, expiry <=24h, modelo explícito, retries=1; chave só no worker.
- [ ] Executar `scripts/preflight_claude.py --process-id <UUID> --version-id <UUID> --tenant-id <UUID>` com `PYTHONPATH=.`. Esse comando é offline: resultado válido declara configuração, não acesso live/conta.
- [ ] Somente após revisão/autorização externa, executar **um job** pelo Worker canônico. Não reenfileirar/force para contornar consumo. Nova tentativa após falha exige nova revisão/aprovação.
- [ ] Confirmar validação factual, claims/fontes, modelo e uso/billing; falha/refusal/truncamento não publica.
- [ ] GET tenant-autorizado: `iaSummary` string igual ao Markdown persistido; outro tenant negado; interface/cópia e exportação UTF-8 autorizadas.
- [ ] Guardar evidência sanitizada (SHA/imagem/run, custos efetivos e validação) e revisão do gerente, sem payload/chaves em Git.

Scripts live sem escopo, inclusive adversarial, estão bloqueados pela mesma
fábrica; um eventual rearmamento requer desenho/autorizações por escopo e
orçamento próprios, não bypass. Nenhum script pago foi executado nesta etapa.

## Resultado e escopo da qualificação

Relatório único desta etapa; SHA/run e hashes finais são registrados em
`%TEMP%/ragvia02b/qualification.json` e no PR draft. Saídas integrais em
`%TEMP%/ragvia02b/`. Testes locais são direcionados, não repetição da suíte
completa da PR #11. A CI existente mantém suas verificações obrigatórias;
se executar a suíte completa no PR, isso cobre a mudança transversal de default
no cliente, schema de wire e migração, não herança automática da aprovação antiga.

**APROVADO quando comprovado nos testes:** bloqueio default/valores insuficientes,
reserva concorrente single-use, timeout sem refund, escopo tenant/sigilo,
schema transformado com validação local preservada e fluxo sintético integral.
**PENDENTE:** evidência da conta, aprovação documental real, limite externo real,
qualidade/compatibilidade do modelo real e revisão jurídica humana.
**BLOQUEADO por design:** qualquer geração paga nesta execução e qualquer
chamada futura sem os requisitos acima. Custo de provedor desta etapa: **USD 0**
(zero chamadas reais; mensagens do SDK são stubs), não estimativa de billing real.
