# RAGVIA-02A — entrega jurídica preparada, Claude real ainda NÃO aceito

Branch: `work/ragvia-02a-claude-preparation`, derivada de
`1dc180306a2f34e3d3f6a43ea8f47f0662a9a60f`. A branch original não foi alterada.

## Documento para avaliação de formato

[Resumo jurídico produzido com provedor simulado](examples/ragvia-02a-synthetic-summary.md).
É uma ação de cobrança **inteiramente sintética**, não o caso do gerente.
Não foi apresentada autorização documental do caso original; nenhum dado desse
caso foi usado. Fonte: `tests/fixtures/judit/ragvia_02a_synthetic.json`, sem
CPF/CNPJ, endereço, contato, anexo ou identificador real. `secrecy_level=0`.

O documento tem identificação, partes sintéticas, classe, assunto, cronologia,
estado atual, pontos de atenção e decisão. Não presume trânsito em julgado,
prazo recursal ou anexos ausentes. Não é parecer nem recomendação de estratégia.
O Markdown é o valor exato publicado em `iaSummary`, renderizado pela aplicação;
JSON estruturado schema-v3 fica separado. A interface interpreta somente o
markup permitido e copia texto legível, não o JSON bruto.

## Alterações mínimas e contratos

- `app/providers.py`: `anthropic_model(default)` aceita `ANTHROPIC_MODEL`
  opcional, validado como identificador limitado; vazio preserva o comportamento.
- `app/rag.py`: o seletor existente passa pelo override. Sem override,
  <=100 movimentos continua Sonnet e >100 continua Opus. Não há novo cliente,
  prompt, schema, caminho de geração, renderizador ou validador.
- Compose local: remove a chave Anthropic do ambiente compartilhado da API/
  scheduler; somente workers recebem chave, modelo, timeout e tentativas.
  Compose produção já isolava a chave e passa a encaminhar o modelo opcional.
  Exemplos `.env` apenas documentam o campo, sem credenciais.
- Novo teste de aceitação substitui **somente** `anthropic_client` por um stub
  de `messages.create`. Exercita o `_generate` real, schema fechado, parsing,
  renderização, validação, persistência, claims/fontes, tenant e API.

Contratos protegidos: `API-FASTAPI-001`, `TENANT-001`, `QUEUE-WORKER-001`,
`HISTORY-001`, `JUDIT-BOUNDARY-001`, `ENV-ISOLATION-001`, e as invariantes de
sigilo/saída estruturada. Nenhum schema de banco foi alterado. Rollback:
reverter o commit desta preparação; desconfigurar o override volta ao roteamento
original. Não substituir configuração persistente de nenhuma instalação.

## Execução sintética e exportação

PowerShell, sem chave real e sem chamadas a provedores:

```powershell
$env:RAGV_02A_ARTIFACT_DIR = "$env:TEMP/ragvia02a-new/document"
python scripts/validate_candidate_postgres.py --output "$env:TEMP/ragvia02a-new/postgres"
python tests/frontend_browser_smoke.py --api-response "$env:TEMP/ragvia02a-new/document/api-response.json"
node tests/frontend_behavior_test.mjs
```

O diretório do executor deve ser novo. O teste produz `summary.md`,
`structured-summary.json`, `api-response.json` e `evidence.json` com hashes,
modelo simulado e validação. O stub usa `claude-acceptance-fixture` para não
rotular o documento como saída de um Claude real. Usage e custo ficam `null`,
não são números de produção fabricados.

A ponte para Chromium é replay da resposta **exata** obtida da API após o banco,
não browser conectado à API ao vivo nem smoke de WebView2. Apresentação e
clipboard são efetivamente exercitados. Para exportar futuramente uma resposta
real já autorizada, usar GET autenticado `/processes/<CNJ>` e gravar somente
`iaSummary` quando `summary.validation.passed=true`, `summary_status=available`
e `iaSummary` for string. Não converter JSON estruturado em Markdown por conta
própria. Guardar o JSON completo somente em artefato privado, se autorizado.

## Identificação e limites do futuro teste Claude

O modelo planejado é `ANTHROPIC_MODEL=claude-sonnet-5`, ID que já integra o
contrato desta base. **Disponibilidade, acesso da conta e compatibilidade real
com Structured Outputs não foram verificados nesta etapa.** Antes da autorização,
confirmar o ID exato provisionado e registrar a referência não sensível dessa
confirmação. Se for necessário outro ID, configurá-lo explicitamente e executar
os contratos/adversarial aplicáveis; não assumir equivalência entre modelos.
Não usar `claude-acceptance-fixture` numa chamada real.

Perfil proposto, a ser aprovado e aplicado somente ao worker isolado:

```text
ANTHROPIC_MODEL=claude-sonnet-5
ANTHROPIC_TIMEOUT_SECONDS=45
PROVIDER_MAX_ATTEMPTS=1
WORKER_TASK_TIMEOUT_SECONDS=120
```

- Cliente existente `anthropic.AsyncAnthropic`, criado exclusivamente por
  `app.providers.anthropic_client`; retries do SDK desabilitados.
- `max_tokens=4000` por resposta; schema fechado, sem tools, sem chat livre.
- Prompt dinâmico limitado por `PROVIDER_PROMPT_MAX_CHARS` (default 120000
  caracteres, **não tokens**), além dos limites de movimentos já existentes.
- Uma regeneração por reprovação: no perfil acima, até duas chamadas por job.
- A fila normalmente permite três execuções: sem limitar o job, até seis
  chamadas nesse perfil. Para a aceitação controlada abaixo, usar **um job com
  `max_attempts=1`**, sem reenqueue/force automático; no máximo duas chamadas.
- Isso limita tentativas/saída, **não implementa um teto monetário**. Antes da
  execução é obrigatório aprovar valor, moeda, tabela vigente do ID exato,
  consumo de entrada/cache/saída e limite de gasto da conta. Se não houver
  controle de orçamento adequado, manter BLOCKED. Timeout não garante ausência
  de cobrança; falhas podem consumir tokens sem telemetria de sucesso.
- `process_summaries` já persiste `model`, `prompt_version`, `usage`, `cache_hit`,
  `generation_ms`, `cost_usd`. Custo pode continuar nulo: reconciliar com billing,
  não interpretar ausência de campo como custo zero.

## Procedimento real reservado — NÃO executado

### 1. Aprovação e isolamento antes de qualquer worker pago

Registrar fora do Git a autorização explícita do gasto e do caso/destinatário,
o limite aprovado, responsável e identificador não sensível da aprovação.
Confirmar tenant autorizado, `secrecy_level=0`, minimização e ausência de
segredos/instruções em campos de origem. Texto processual continua não confiável.
Para o caso original sem essa documentação, permanecer no sintético.

Preparar um projeto Docker novo `ragvia02a-<run>`, portas livres de loopback,
PostgreSQL/pgvector por digest e storage descartável, sem reutilizar volumes,
bancos ou `.env` habituais. Usar o `compose.yaml` existente com um override
privado de isolamento: PostgreSQL fixado no digest usado pelo executor 01D,
`volumes: !reset []` e tmpfs para dados; API com `ports: !override` para uma
porta loopback exclusiva. Os serviços de aplicação devem usar o mesmo artefato
imutável testado. Registrar SHA, árvore e identidade/digest desse artefato.
Não promover esse ambiente de aceitação local a produção; produção mantém o
Compose endurecido e o proxy de egress existentes.

Manter ambos os workers e o scheduler parados inicialmente. Subir somente
PostgreSQL, migração e API. Não iniciar aquisição Judit ou DataJud. Desabilitar
attachments, DataJud, reranker e providers de embedding externos; escolher caso
curto, sem anexos, dentro do bypass de retrieval existente. O caso sintético tem
quatro movimentos. Um caso longo exige aceitação de retrieval separada.

A chave deve vir de secret manager/arquivo privado restrito e entrar **somente
no ambiente do worker**. Não passá-la em argumentos, URL, clipboard, frontend,
`docker compose config` publicado, shell trace, issue ou relatório. Conferir
Compose renderizado privadamente sem imprimir valores. API e scheduler não
podem herdar a chave. Não reutilizar o `.env` da instalação habitual.

### 2. Ingestão sem pagar Judit

Usar um bundle autorizado já exportado no formato aceito por
`scripts/replay_judit_webhooks.py`, com dois eventos: `response_created`
(`payload.response_data` é o JSON Judit minimizado) e `request_completed`.
Bundle schema 1, kind `judit_webhook_replay_bundle`, `cnj_sha256` do CNJ canônico,
`deliveries` com cada envelope de webhook em `payload`. Identificadores de
request/response/callback devem ser exclusivos do run. Não inventar autorização
porque um bundle é sintaticamente válido. Vincular o webhook ao tenant exclusivo
via `JUDIT_WEBHOOK_TENANT_ID` antes do replay; configurar autenticação gerenciada
para esse tenant e um tenant negativo. Não iniciar os workers ainda.

```text
python scripts/replay_judit_webhooks.py --bundle <bundle-privado> --cnj <CNJ-autorizado> --base-url http://127.0.0.1:<porta-exclusiva>
```

Fornecer o segredo do webhook pelo ambiente, nunca pela flag. Esse comando
atinge somente a API local e **não consulta Judit**. Não usar o live smoke Judit.
Confirmar apenas um job `finalize_judit_request` pendente nesse banco novo.

### 3. Geração canônica, somente após autorização explícita

Usar um worker efêmero do mesmo artefato, em vez de iniciar o daemon. O seguinte
é o corpo Python para executar **dentro do serviço worker-1 isolado** (`docker
compose ... run --rm --no-deps -T -e CLAUDE_ACCEPTANCE_APPROVAL_REF worker-1 python -`), nunca na API. O operador
revisa o banco e a aprovação antes de executar. A variável de referência não
substitui a autorização documental. Não executar este bloco nesta preparação.

```python
import asyncio, os
from app.db import create_pool
from app.worker import Worker, WorkerSettings

async def main():
    assert os.environ.get("CLAUDE_ACCEPTANCE_APPROVAL_REF")
    assert os.environ["ANTHROPIC_MODEL"] != "claude-acceptance-fixture"
    assert os.environ["PROVIDER_MAX_ATTEMPTS"] == "1"
    pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        worker = Worker(pool, WorkerSettings.from_env())
        async with pool.acquire() as conn:
            jobs = await conn.fetch("SELECT id, task_name FROM jobs WHERE status='pending'")
            assert len(jobs) == 1 and jobs[0]["task_name"] == "finalize_judit_request"
        assert await worker.process_one()  # ingestão/finalização existente
        async with pool.acquire() as conn:
            jobs = await conn.fetch("SELECT id, task_name FROM jobs WHERE status='pending'")
            assert len(jobs) == 1 and jobs[0]["task_name"] == "generate_process_summary"
            await conn.execute("UPDATE jobs SET max_attempts=1 WHERE id=$1 AND status='pending'", jobs[0]["id"])
        assert await worker.process_one()  # chama o provedor REAL: até duas respostas
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM jobs WHERE status<>'completed'") == 0
    finally:
        await pool.close()

asyncio.run(main())
```

Se houver erro, interromper; não reexecutar automaticamente. Inspecionar estado
sanitizado e billing antes de nova autorização. Não desativar validação, inventar
claims nem publicar texto de uma segunda reprovação. O teste sintético usa
exatamente esses Worker/handlers, com o cliente Anthropic substituído apenas na
fronteira de rede.

### 4. API, documento e avaliação humana

Consultar `/processes/<CNJ>` com o tenant autorizado, confirmar validação,
`iaSummary` string igual a `summary.markdown`, modelo/prompt, persistência e
proveniência vinculada. Confirmar negação para outro tenant. Abrir a interface
servida pela mesma API local, verificar seções/datas e copiar o documento.
Exportar esse Markdown exato para arquivo privado UTF-8; não publicar o caso.
Guardar hashes, SHA/artefato/run, aprovação não sensível, resultado de validação,
usage/billing, duração e revisão do gerente. Nunca incluir headers/segredos.

## Evidência e limites desta preparação

Run local `ragvia02a`, logs/artefatos em `%TEMP%/ragvia02a/`.
Comandos: executor PostgreSQL acima; suíte Linux
`python -m pytest -q tests --ignore=tests/integration --hypothesis-seed=20260918`;
`python scripts/project_harness.py`; `python -m ruff check app scripts tests`;
frontend Node/Chromium e avaliações offline existentes. Imagem de testes Linux:
`sha256:c0f369a87fe34768297e6058d968f316427bbfa405771defd8b2c641134d24a1`.
PostgreSQL descartável do executor:
`pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`.

Resultados observados no checkout: 1273 unitários Linux, 168 integrações
PostgreSQL sem skips, browser/cópia, frontend Node e Compose local renderizado
com chave sintética somente nos workers. O primeiro novo teste falhou por
atributo ausente no estado ASGI sem lifespan; sua configuração de teste foi
corrigida, sem modificar expectativas funcionais. A validação do SHA final e a
CI devem acompanhar o commit efetivamente executado, não herdar o verde do 01D.

Limitações: fatos da fixture e resposta do stub foram escritos para teste;
`passed=true` não prova qualidade jurídica ou compatibilidade Claude real.
As fontes de cada item cronológico/decisão são persistidas, mas vínculo formal
e verificações determinísticas não provam todas as inferências semânticas.
Ausência no recorte não significa inexistência nos autos. Não há informação de
prazo, recurso, trânsito, representantes ou anexos além da fixture; não preencher
lacunas com suposições. O controle de sigilo usa o caminho local existente e
os testes negativos existentes, nunca um processo real sigiloso.

**RAGVIA-02 final permanece PENDENTE:** exige saída do Claude real, validada,
persistida e disponibilizada pela API e interface, com avaliação humana e
custo autorizado. Esta preparação não executa chamadas pagas, live adversarial,
Judit ou DataJud externos; o adversarial ao vivo, quando exigido pelo contrato
de provedor, necessita orçamento/autorização próprios.
