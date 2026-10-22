# RAGVIA-01D — qualificação da candidata

## Escopo e contratos

Base inicial: `5cc2cc7bc3a2dc5d295846ddadb998f9d96e888c`.
Branch: `migration/ragvia-01-candidate`. Remoto: `oigorbrito/RAgvia`.
Contratos: `API-FASTAPI-001`, `TENANT-001`, `QUEUE-WORKER-001`,
`HISTORY-001`, `JUDIT-BOUNDARY-001`, `ENV-ISOLATION-001`.
Nenhum código de produção, SQL, esquema ou expectativa funcional foi alterado.
Rollback: reverter este commit; nenhuma migração ou transformação de dados é necessária.

## Diagnóstico recuperado

O registro da execução 01C foi recuperado da sessão local de 2026-10-22,
`01a161df-96dc-7401-ab04-5cad00085d84`.
Os dez erros eram fixtures de `test_api_postgres.py`, em `migrate()` / conexão
asyncpg, antes das asserções. Ocorreram `unexpected connection_lost()` durante
negociação SSL e `CannotConnectNowError: the database system is starting up`.
A falha de `test_api_keys_enforce_scope_rate_limit_lifecycle_and_safe_audit`
também ocorreu em `migrate()` com `unexpected connection_lost()`: **não era uma
falha funcional de API key**. Resultado original: 156 aprovados, 1 falha, 10 erros.

O comando original sondava `pg_isready` dentro do contêiner (socket Unix),
usava tag mutável, publicação de porta sem loopback e não fazia o esgotamento
do readiness abortar a execução. O endpoint efetivo usado por asyncpg ainda
estava iniciando. Readiness interno não prova readiness TCP externo;
o servidor temporário do entrypoint é uma explicação compatível, não uma
observação comprovada por logs antigos do contêiner (já removido).
Não há evidência de esgotamento do pool ou bug de encerramento neste incidente:
a falha antecede sua criação. Não se adicionaram retries à aplicação para mascará-la.

Correção: `scripts/validate_candidate_postgres.py` cria somente recurso próprio,
com nome aleatório, imagem por digest, tmpfs e porta efêmera em `127.0.0.1`.
Espera `SELECT 1` pela mesma URL TCP/credenciais dos testes, com deadline;
configura URLs de teste/admin/restore e `DATABASE_URL` somente para seu contêiner;
salva JUnit, saída integral, estado Docker e logs, e remove o recurso em `finally`.
Suíte vazia, falhas, erros ou qualquer skip impedem qualificação.
Testes cobrem readiness SQL, fechamento, deadline e rejeição de skips/erros.
CI também verifica o JUnit para impedir aprovação com integração ignorada.

## Segurança

As quatro alterações pré-existentes foram preservadas e revisadas:
`docs/deployment/quickstart.md`, `tests/test_api_v1_strict_json.py`,
`tests/test_log_safety.py`, `tests/test_log_safety_redaction_slice.py`.
Os exemplos usam a variável do token local; fixtures sintéticas foram ajustadas.
As duas asserções restantes com literais scanner-shaped foram substituídas
por variáveis construídas em runtime. Os valores testados e a exigência de
redação integral permanecem os mesmos. Não há allowlist nem alteração global de scanner.

Gitleaks 8.30.1: distinguir três escopos. O commit-base histórico tem 11 alertas
em exemplos/fixtures sintéticas; não são evidência de credenciais reais e o
histórico não foi reescrito. O diretório de trabalho inicialmente tinha quatro
alertas adicionais em caches ignorados (`__pycache__`, `.pytest_cache`).
Esses caches não são arquivos publicáveis. A árvore final é exportada com
`git archive HEAD`, sem caches; scanner deve passar tanto nessa árvore quanto
no diff do commit final (`--log-opts="-1 HEAD"`). Não publicar relatórios com
valores de tokens; todos os relatórios locais usam `--redact`.

## Evidência executada localmente

Run local: `ragvia01d`, 2026-10-22. Evidências integrais em
`C:/Users/igorb/AppData/Local/Temp/ragvia01d/` (não versionadas).
Estes resultados qualificam somente o checkout executado; não representam CI verde.
O SHA final e o hash da árvore são obtidos após o commit e registrados no resultado da execução.

| Verificação | Resultado observado |
|---|---|
| Integração PostgreSQL inicial | 167 aprovados; zero skips |
| Integração pelo novo executor | 167 aprovados; zero erros, falhas ou skips; `pg-runner/postgres.xml`, `run.json` |
| Testes direcionados segurança/executor | 98 aprovados |
| Suíte Linux final | 1264 aprovados, incluindo `test_attachment_sandbox.py`; zero skips |
| Harness e Ruff | aprovados |
| Frontend Node e browser original | aprovados |
| API canônica → browser/cópia | aprovado, resposta exportada por `test_pipeline_e2e.py` |
| Avaliações sintéticas RAG, pipeline offline e geração offline | baselines aprovadas no Windows e Linux |
| Build da imagem e `verify_image_runtime.sh` | aprovados |
| Restore separado | checksum relocável, leitura de archive, sentinela, 35 migrações e pgvector aprovados |

Ambiente: Windows Python 3.12.10 / Docker Desktop Linux; não se exigiu conversão
WSL1. Imagem de testes Linux reutilizada por identidade imutável local
`sha256:c0f369a87fe34768297e6058d968f316427bbfa405771defd8b2c641134d24a1`
(Python 3.12.14; dependências inventariadas no log local).
PostgreSQL/pgvector:
`pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`.

A primeira tentativa Linux encontrou a configuração herdada
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; foi removida no ambiente do teste.
Outra tentativa usou bind read-only, incompatível com um teste que escreve um
arquivo temporário no root do checkout. A execução final copiou o checkout
para filesystem descartável gravável e excluiu bytecode Windows antes de testar.
Nenhuma expectativa de produto foi relaxada.
Os scripts de avaliação requereram `PYTHONPATH=.` neste ambiente Windows;
sem isso resolviam outro pacote instalado. Não é falha de geração.

Docker estava parado e foi iniciado. Isso também reativou serviços com política
de restart já configurada no host; não se executaram comandos/consultas nos
bancos habituais, nem se alteraram seus volumes/configuração. Toda atividade
SQL desta qualificação foi direcionada aos contêineres próprios descartáveis.

## Aceitação jurídica sintética

Não foi criado pipeline paralelo: `test_pipeline_e2e.py` usa webhook Judit,
worker/fila, ingestão, contexto, geração simulada, validação com regeneração,
persistência real e GET autenticado da API. Confirma
`iaSummary == summary.markdown`, validação aprovada e acesso auditado.
A exportação opcional `RAGV_TEST_API_RESPONSE_PATH` grava apenas essa resposta
sintética, sem headers ou segredos. O browser consome exatamente esse artefato,
renderiza o resumo e verifica conteúdo legível na área de transferência.
A ponte API/browser é replay de resposta HTTP, não uma sessão desktop WebView2
nem conexão browser→API ao vivo; a API e o banco já foram exercitados na etapa anterior.

Cobertura complementar executada: schema-v2 em `test_api_v1_postgres.py`,
schema-v3 e compatibilidade v2 em `test_summary_output.py`, separação JSON/Markdown
no frontend, tenant/sigilo em integração, proveniência/cronologia e bloqueio
em `test_invalid_summary_publication.py` / `test_validation_persistence_postgres.py`.
Simulações demonstram contratos/orquestração, **não qualidade ou compatibilidade
Claude real, latência, custo, throughput ou RTO/RPO**. As avaliações registram
claims semanticamente não verificados; baseline aprovada não prova verdade jurídica.

## Reprodução e publicação

```powershell
python scripts/validate_candidate_postgres.py --output C:/Temp/ragvia-new-run
# Opcional, antes do executor: definir RAGV_TEST_API_RESPONSE_PATH para um arquivo novo.
python tests/frontend_browser_smoke.py --api-response C:/Temp/canonical-api.json
python scripts/project_harness.py
python -m ruff check app scripts tests
node tests/frontend_behavior_test.mjs
```

A saída deve ser um diretório novo; não passar URLs de bancos existentes.
O executor não recebe URL externa por argumento.
Para avaliações diretas no Windows definir `PYTHONPATH=.`.
A validação Linux usa `--network none`, cópia descartável do checkout e a imagem
identificada acima, com `PYTHONPATH` apontando a cópia e autoload de plugins habilitado.

O remoto estava vazio, sem runs CI nem rulesets listados. O workflow foi
habilitado também para push **somente desta branch candidata**, pois não existe
base remota para abrir PR ainda. Não criar/popular `main`, alterar proteções,
fazer merge ou release. Publicação de branch não equivale a qualificação CI:
o resultado efetivo do run deve acompanhar o SHA publicado. Se infraestrutura
externa impedir CI, registrar BLOCKED em vez de declarar verde.

Claude real fica reservado: obter autorização explícita, chave configurada
somente no worker, JSON autorizado não sigiloso minimizado, ambiente isolado,
modelo identificado e payload dentro da fronteira documentada; executar geração
canônica com validação/citações, persistência e apresentação, guardar evidência
sanitizada e custo. Nenhuma chamada Claude, Judit paga ou DataJud externa foi feita.
