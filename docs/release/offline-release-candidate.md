# Offline release evidence — v0.1.0

Este documento registra a qualificação offline do release histórico `v0.1.0` e não substitui validação no ambiente de destino. O estado do `main` posterior a essa tag é acompanhado separadamente em `docs/release/next-release.md`.

## Caminho canônico

Com Git, Docker e Docker Compose instalados, execute da raiz:

```powershell
.\scripts\smoke_offline.ps1
```

```sh
./scripts/smoke_offline.sh
```

O smoke cria PostgreSQL 16/pgvector, aplica migrations, executa API ASGI,
fila/worker, webhook sintético, finalização, validação/persistência de summary
e leitura autenticada. Usa dados sintéticos e não exige chaves pagas.

## Definition of Done

- [x] clone novo validado em ambiente externo limpo com Git + Docker + Docker Compose;
- [x] pré-requisitos documentados;
- [x] project harness (incluindo guardrails de migration/release), unitários, integração PostgreSQL, image smoke e backup/restore verdes;
- [x] smoke offline termina com `RPY OFFLINE SMOKE: PASS` e `providers=0`;
- [x] invariantes de dados, concorrência/recovery e sigilo evidenciados;
- [x] caminho vetorial com mais de 40 movimentos evidenciado offline;
- [x] contratos de providers testados com fakes e sem rede;
- [x] frontend comportamental offline verde;
- [x] exemplos de configuração conferidos contra o runtime;
- [x] PRs necessários integrados e CI do head exato verde;
- [x] workflow de imagem produz provenance attestation verificável para o digest publicado.

O fresh clone foi validado em Windows fora do ambiente de desenvolvimento anterior:
a imagem foi reconstruída, migrations 001-014 foram aplicadas e o smoke terminou com
`RPY OFFLINE SMOKE: PASS`, `summary=valid`, `jobs=complete` e `providers=0`.

A versão formal deste primeiro release offline é `0.1.0`. A tag histórica
`v0.1.0` já existe e é imutável; nenhuma mudança posterior de `main` faz parte
desse release. O identificador do próximo release permanece `TBD`.

## Matriz de evidências

| Capacidade | Evidência | Offline | Estado |
|---|---|---:|---|
| Guardrails de projeto | `scripts/project_harness.py` (inclui migration/release harness) | sim | repository gate |
| Fila/recovery | testes PostgreSQL de queue/worker | sim | repository gate |
| Retrieval >40 | E2E PostgreSQL com embeddings fake/pgvector | sim | repository gate |
| Sigilo | testes E2E/local determinístico | sim | repository gate |
| Provider contracts | fakes e monkeypatches | sim | repository gate |
| Pipeline operacional | `scripts/smoke_offline.*` | sim | repository gate |
| Frontend | harness Node comportamental | sim | repository gate |
| Fresh clone externo | smoke em checkout limpo no Windows | sim | passed |
| Provenance da imagem | GitHub artifact attestation do digest publicado | não | release workflow |
| Judit real | provider acceptance controlado | não | deferred |
| Anthropic real | provider acceptance controlado | não | deferred |
| OpenAI legacy real | provider acceptance controlado, somente se selecionado | não | deferred |
| Cohere Embed/Rerank reais | autorização específica + provider acceptance | não | deferred |
| BGE embeddings/reranker reais | artifact/verifier/reindex/benchmark conforme aplicável | sim | deferred operational evidence |

## Provider Acceptance — POST-OFFLINE-RELEASE / NON-BLOCKING

A aceitação real é ambiente-específica e segue `docs/release/provider-acceptance.md`.
Ela deve usar credenciais rotacionáveis, orçamento controlado, processo/tenant
explicitamente autorizado e evidência sanitizada. Não executar chamadas pagas no CI
nem tratar a qualificação offline como autorização para indexação em massa ou uso de
providers externos.

## Segurança e operação

Nunca commite ou cole chaves em issues, PRs ou logs. Use `.env.production.example`
apenas como template e um secret manager para produção. Consulte os runbooks
de [produção](../deployment/production.md) e [backup/restore](../deployment/backup-restore.md).
