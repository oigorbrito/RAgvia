# RagV

**RagV is a legal-process research and summarization system built on a FastAPI backend, PostgreSQL 16 and pgvector, with tenant isolation, durable job processing, provenance, and a provider-free confidential path.**

RagV is deliberately presented first as a **reliable legal-process system**, not as an LLM demo.

Its core engineering surface includes:

- HTTP/API boundaries;
- PostgreSQL data modeling;
- concurrent workers;
- durable job ownership;
- retry / fencing / reclaim;
- tenant authorization;
- idempotent webhooks;
- retrieval;
- provenance;
- backup / restore;
- CI and operational validation.

RAG and external providers sit inside those boundaries rather than replacing them.

## Current evidence

The historical `v0.1.0` release established a qualified offline path. The current `main` contains subsequent work for the next release, whose identifier remains `TBD`.

The canonical offline validation rebuilds the image, applies migrations, exercises API/queue/workers with synthetic data, and finishes with:

```text
RPY OFFLINE SMOKE: PASS
process=0000000-00.2026.8.21.0001
summary=valid
jobs=complete
providers=0
```

That evidence supports the **offline/provider-free path**.

It does not automatically establish acceptance for real Judit, Anthropic, OpenAI, Cohere, or embedding-provider paths. Those remain separate Provider Acceptance boundaries.

```text
OFFLINE_PASS != PROVIDER_ACCEPTANCE
PROVIDER_ACCEPTANCE != PRODUCTION_READINESS
```

See:

- `docs/release/offline-release-candidate.md`
- `docs/release/provider-acceptance.md`
- `docs/release/v0.1.0.md`

## Architecture

```text
Browser / API
      │
      ▼
   FastAPI
      │
      ├──────────────> Judit (optional external acquisition)
      │
      ▼
PostgreSQL 16 + pgvector
      │
      ├── durable job queue
      ├── tenant/process authority
      ├── process data + provenance
      └── embeddings / retrieval data
      │
      ▼
   workers (2+)
      │
      ├── retry / fencing / reclaim
      ├── retrieval
      └── optional external model/provider path

scheduler / reclaimer
      └── advisory-lock protected maintenance
```

### PostgreSQL as data store and coordination layer

The project intentionally avoids introducing a separate broker while the current workload does not justify one.

Workers claim jobs using:

```sql
FOR UPDATE SKIP LOCKED
```

with explicit ownership, heartbeat, retry, fencing, and reclaim semantics.

### Retrieval

For smaller process histories, the system can use the full movement set without embeddings.

For larger histories, the current path combines lexical retrieval with pgvector and preserves forced-inclusion rules for recent and legally relevant movements.

### Confidential path

Processes under secrecy/confidentiality rules do not send their content to external LLM or embedding providers.

```text
CONFIDENTIAL
    ↓
provider-free deterministic path
```

## Engineering invariants

- PostgreSQL is the source of truth.
- Queue state is durable application state, not transient worker memory.
- Concurrent claims use explicit ownership.
- External callbacks are idempotent.
- Tenant authority is resolved before process access.
- Sensitive paths fail closed.
- Migrations and recovery are tested as operational concerns.
- Provider availability is not required to qualify the offline baseline.
- New infrastructure is not added simply because it is conventional.

## Quickstart offline

Pré-requisitos:

- Git;
- Docker;
- Docker Compose;
- Docker daemon em execução.

O host não precisa de Python, pytest nem chaves de providers para o smoke offline.

### Windows

```powershell
git clone https://github.com/oigorbrito/RagV.git
cd RagV
.\scripts\smoke_offline.ps1
```

### Linux/macOS

```bash
git clone https://github.com/oigorbrito/RagV.git
cd RagV
./scripts/smoke_offline.sh
```

O smoke usa projeto Compose, rede e volume descartáveis próprios. Não use comandos globais destrutivos de limpeza do Docker para executar ou encerrar esse fluxo.

## Desenvolvimento local

Para trabalhar fora do container, use Python 3.12 e instale as dependências sob o arquivo de constraints:

```bash
python -m pip install pip==26.2.1
python -m pip install --constraint requirements/constraints.txt -e '.[dev]'
```

Checks rápidos:

```bash
python scripts/project_harness.py
pytest -q tests --ignore=tests/integration
node tests/frontend_behavior_test.mjs
```

Integração PostgreSQL:

```bash
pytest -q tests/integration
```

Antes de abrir PR, leia `CONTRIBUTING.md`. Mudanças arquiteturais, de migration, fila, retrieval, provider, sigilo ou deployment também devem respeitar `AGENTS.md`.

## Demo local sem custo

Para testar o frontend e o fluxo principal sem Judit, Anthropic, OpenAI ou Cohere,
use o seed sintético local. Ele grava somente dados fictícios, usa o tenant do
`dev-local-token` e persiste um resumo já validado com custo registrado como zero.

Com a stack local já migrada:

```powershell
docker compose --profile demo run --rm demo-seed
```

Saída esperada:

```text
RPY LOCAL DEMO: READY
process=0000000-00.2026.8.21.0001
token=dev-local-token
providers=0
```

Depois abra `http://localhost:8000/`, informe `dev-local-token` e consulte
`0000000-00.2026.8.21.0001`.

O serviço `demo-seed` força as credenciais de providers externos para vazio e
falha se o script for executado com credenciais externas habilitadas. Ele pertence
somente ao Compose de desenvolvimento; `compose.production.yaml` não contém esse
serviço.

## Modo com providers reais — opcional

Use apenas ambiente controlado, credenciais rotacionáveis e orçamento explícito. Nunca use chaves reais no CI ou em exemplos commitados.

Exemplo de variáveis esperadas:

```bash
export ANTHROPIC_API_KEY='change-me'
export OPENAI_API_KEY='change-me'
export JUDIT_API_KEY='change-me'
export JUDIT_WEBHOOK_TOKEN='change-me'
export RPY_BEARER_TOKENS='{"example-only":"00000000-0000-0000-0000-000000000000"}'
```

Então:

```bash
docker compose up --build
```

A stack local sobe PostgreSQL/pgvector em `localhost:5432`, migration job, API/frontend em `localhost:8000`, dois workers e exatamente um scheduler.

Health/readiness:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Abra `http://localhost:8000/` para usar a interface.

## Fluxo do produto

A interface web é servida pela própria API em `/`. O usuário informa um bearer token provisionado para seu tenant e um número CNJ. O fluxo suportado é:

1. consultar um processo já autorizado;
2. se o CNJ ainda não estiver disponível, solicitar aquisição à Judit;
3. acompanhar a chegada dos dados processuais;
4. ler partes, assuntos, contexto e movimentações quando a versão estiver disponível;
5. acompanhar a geração enquanto `summary_status=processing`;
6. ler/copiar o resumo validado quando publicado.

O browser não recebe credenciais da Judit nem identificadores internos de request/job. O bearer token permanece somente em memória da página e não é salvo em `localStorage`/`sessionStorage`.

## Contrato HTTP principal

- `GET /processes/{cnj}`: lê apenas processo autorizado ao tenant e retorna `404` fora do escopo.
- `POST /processes/{cnj}/request`: solicita aquisição de CNJ ausente, com idempotência por tenant/CNJ; retorna somente estado público.
- `POST /webhooks/judit/{token}`: recebe callbacks assíncronos da Judit.
- `GET /health`: liveness.
- `GET /ready`: readiness com PostgreSQL.
- `GET /ops/metrics`: métricas protegidas por credencial operacional separada.

O estado público do resumo é `available`, `processing`, `not_generated` ou `unavailable`. Detalhes internos de fila, tentativas, worker, provider e erros não fazem parte do contrato público.

## Webhook Judit

- token inválido retorna `404`;
- `callback_id` é persistido para idempotência;
- `response_created` do tipo `lawsuit` é staged e concede acesso somente aos tenants correlacionados à solicitação;
- `request_completed` enfileira promoção no worker;
- resposta fresca (`cached_response=false`) vence a cacheada;
- resposta apenas cacheada pode ser promovida, mas não dispara LLM;
- o handler HTTP não executa geração nem promoção pesada.

## Retrieval

- até 40 movimentos: todos entram no contexto, sem embeddings;
- acima de 40: BM25 real + pgvector com pesos `0.5 / 0.5`;
- boost de recência;
- primeiro, último e cinco movimentos mais recentes são force-included;
- milestones judiciais relevantes são force-included independentemente do score.

## Segurança e LGPD

- autorização de portfólio via `tenant_processes`;
- bearer token resolve tenant antes da leitura ou solicitação;
- solicitação Judit é correlacionada de forma durável ao tenant antes de callbacks concederem acesso;
- processos sob sigilo usam caminho determinístico local e não enviam conteúdo para embeddings/LLM externos;
- `access_log` é imutável;
- expurgo remove versões, JSONB, movimentos, vetores e callbacks brutos da Judit, preservando histórico de acesso.

Não publique vulnerabilidades, credenciais ou dados processuais sensíveis em issues. Consulte `SECURITY.md`.

## CI e evidência de release

O GitHub Actions executa:

- validação do contrato de Compose de produção;
- project harness (que agrega os guardrails de migration/release e verifica o wiring das demais evidências);
- testes unitários;
- harness comportamental do frontend;
- smoke da imagem;
- drill de backup/restore;
- integração PostgreSQL;
- smoke offline provider-free.

A suíte E2E cobre CNJ ausente → solicitação → callback → acesso tenant-scoped → finalização → resumo validado, além de callbacks fora de ordem/retry, resposta cached sem LLM, sigilo provider-free e retrieval vetorial com mais de 40 movimentos.

## Operação e deployment

Produção usa `compose.production.yaml`, imagem imutável por digest e credenciais PostgreSQL separadas por responsabilidade.

Documentação operacional:

- `docs/deployment/local-offline.md` — validação local provider-free;
- `docs/deployment/production.md` — topologia e deployment;
- `docs/deployment/backup-restore.md` — backup e restore drill;
- `docs/release/next-release.md` — readiness do próximo release;
- `docs/release/versioning.md` — política de identidade/versionamento;
- `docs/release/offline-release-candidate.md` — evidência histórica do `v0.1.0`;
- `docs/release/provider-acceptance.md` — aceitação controlada de providers/artifacts reais;
- `docs/release/v0.1.0.md` — release notes históricas;
- `docs/engineering/empirical-engineering.md` — política de evidência técnica;
- `docs/engineering/project-harness.md` — contrato executável do harness do projeto.

## Estrutura do repositório

```text
app/           aplicação FastAPI, fila, retrieval, RAG e frontend
sql/           migrations PostgreSQL
scripts/       harnesses, validações e operações
tests/         unitários, frontend e integração PostgreSQL
docs/          engenharia, deployment e release
requirements/  constraints reprodutíveis
```

## Limites atuais do MVP

- autenticação por bearer token provisionado; sem login/autocadastro;
- sem dashboard, favoritos, alertas ou gestão de carteira;
- polling do frontend é limitado;
- providers reais exigem infraestrutura, credenciais válidas e aceitação operacional separada;
- o projeto evita deliberadamente Redis/Celery, vector DB externo e frameworks RAG pesados enquanto o stack atual for suficiente.

## Contribuição e segurança

- engenharia e PRs: `CONTRIBUTING.md`;
- contrato para agentes/coding assistants: `AGENTS.md`;
- reporte responsável de vulnerabilidades: `SECURITY.md`.

## Licença e atribuições

O RagV é distribuído sob a licença MIT. Consulte `LICENSE` para os termos do projeto e `NOTICE` para provenance e atribuições de componentes/implementações de terceiros.
