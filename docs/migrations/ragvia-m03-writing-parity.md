# RAGVIA-M03-F — Paridade de redação jurídica

## Decisão

**M03 aprovado no escopo de paridade funcional sintética desta PR.** A matriz automática não tem omissões: os requisitos cobertos estão `COVERED`; as duas divergências abaixo estão explicitamente justificadas. Isso não é evidência de qualidade de geração live. A CI completa passou para o SHA `d4a8205b0f08eab62291e7c974e8dfa58848b8c6` (run `40057106691`). A PR #15 continua draft conforme solicitado; sem merge.

- Branch: `work/ragvia-m03-writing-parity`, empilhada na cabeça da PR #14 (`work/ragvia-m02-knowledge-parity`).
- Commit inicial preservado: `33a71c33a93438cd296e783e33bddfbc11b42b17`.
- Doador: `oigorbrito/rag_resumo@0d0f8cbb495292a3f3d61fba75428f3136059ea8`.
- Fixture única: `tests/fixtures/judit/parser_parity_synthetic.json`; SHA-256 `badc50e69b79be15f4334f555d679a1794ff0c4d6e2bbd111eab18c579180194`.

## Comparação automática doador × destino

O gerador template **original** e o renderer original do doador foram executados sobre a fixture acima. A saída estruturada e o documento do doador, com proveniência e hash da fixture, estão em `docs/evaluation/evidence/m03-donor-template.json`. O destino foi gerado pela integração canônica M01→M02→M03, via PostgreSQL, usando um SDK Anthropic simulado e a mesma fixture; documento resultante em `docs/evaluation/evidence/m03-synthetic-summary.md`.

Reproduzir a captura do doador e a comparação:

```bash
python scripts/capture_donor_writing_reference.py --donor-path ../ragresumo-m03-reference
python scripts/evaluate_writing_parity.py
python -m pytest -q tests/test_writing_parity_evaluation.py
```

A comparação valida cobertura de fatos/eventos e não igualdade literal de frases. O arquivo `docs/evaluation/evidence/m03-writing-coverage.json` é a matriz máquina-legível produzida pela execução. Além dos casos cobertos, o teste de mutação remove um nome do documento destino e exige `FAIL_DESTINATION_OMISSION`.

| Requisito | Resultado | Verificação / diferença |
|---|---|---|
| Identificação CNJ | COVERED | Identificador presente nos dois documentos. |
| Partes e polos registrados | COVERED | Todos os nomes e polos explícitos aparecem; destino usa rótulo neutro “polo cadastrado”. |
| Relações parte–representante | COVERED | Relações `representa` efetivamente registradas aparecem no destino. |
| Classe principal e assuntos | COVERED | Classe principal e todos os assuntos aparecem. |
| Explicações de classe/assunto | COVERED | Destino usa trechos recuperados M02; cada definição é verificada no contexto da integração. Não usa explicações legais genéricas do template doador. |
| Datas, audiência e cronologia | COVERED | Data do ato (01/09/2026) fica separada da data/horário agendado (03/03/2027, 15:00); linha do tempo em ordem crescente. |
| Citações/intimações e atos administrativos | COVERED | Intimações e expediente são agrupados com contagens, destinatários, finalidade/prazo registrado e datas; remessas, juntada e demais marcos preservados. |
| Decisão/sentença não presentes | COVERED | Atenção declara limite do recorte e não afirma que os atos ocorreram. |
| Documentos pessoais | COVERED | Nenhum documento pessoal limpo no Markdown. Teste de payload verifica que dados documentais e OAB não chegam ao provider. |
| Classe secundária “EMBARGOS” marcada suspeita | DIVERGENCE_JUSTIFIED | O doador oferece explicação para essa classificação; M01 a sinaliza como suspeita e o contrato público canônico expõe a classe principal. Não promover heurística a fato processual. |
| Inferências sobre polo | DIVERGENCE_JUSTIFIED | O template doador presume prejuízo no polo ativo e responsabilidade no polo passivo. O destino limita-se ao polo cadastrado, sem afirmar mérito/alegação por inferência. |

## Introdução, cronologia e compatibilidade

O contrato `synthesis` continua uma string no schema-v3 e nos documentos schema-v2 legados. O parser canônico normaliza quebras, portanto não se exige nem se anuncia dois parágrafos físicos. Para assegurar duas funções distintas, a síntese pública nova precisa trazer os segmentos não vazios `Contexto do processo:` e `Andamento processual:`. O prompt v7 os instrui e o validador rejeita a publicação se faltar qualquer segmento; teste unitário cobre aceitação/rejeição e a integração persiste somente resultado validado. Registros antigos permanecem legíveis; nenhuma migração de schema foi necessária. A via secreta permanece local e inalterada.

A timeline de demonstração mostra a data de prática em cada evento e, para audiência, separa explicitamente a data futura de agendamento, sem afirmar realização. As intimações repetidas e entradas administrativas são agrupadas; o avaliador verifica contagens/destinatários e mantém distribuição, audiência, juntada, remessas e demais marcos. A saída segue estruturada; nenhum HTML livre, `tool_use`, renderer ou fallback/template do doador entrou no runtime. `app.rag` continua único proprietário de geração, validação, persistência e retry.

## PostgreSQL, API e navegador

O percurso integrado usa a fixture M01 congelada, recuperação M02 e cliente Anthropic simulado. PostgreSQL descartável: `pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`. A resposta exportada é validada e `iaSummary == summary.markdown`; o Markdown salvo no documento demonstrativo foi comparado exatamente com ambos.

Para reproduzir a demonstração e o browser smoke diretamente conectado à API local (sem stub de rota de processo no browser), use `TEST_DATABASE_URL` apontando para PostgreSQL descartável:

```bash
RAGV_M01_ARTIFACT_DIR=/tmp/ragvia-m03-final \
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/rpy_test \
python -m pytest -q tests/integration/test_legal_knowledge_parity_postgres.py

TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/rpy_test \
python scripts/run_m03_browser_smoke.py \
  --api-response /tmp/ragvia-m03-final/api-response.json \
  --browser-auth /tmp/ragvia-m03-final/browser-auth.json
```

O browser abre a UI servida pela API FastAPI local, lê o processo persistido com Bearer de teste e exercita copiar. A cópia deve coincidir com o Markdown validado após normalizar somente `CRLF`/`LF`; datas, nomes sintéticos e fatos são comparados. O token é sintético e a API/browser não acessam Judit ou Claude.

## Qualificação executada

Ambiente: Python 3.12, Chromium/Playwright local, PostgreSQL 16 com pgvector no digest acima. Execuções no worktree da branch desta PR; as fixtures/provedor são sintéticos.

- Captura doador exato e comparação: `capture_donor_writing_reference.py` + `evaluate_writing_parity.py` — matriz aprovada com `COVERED` ou divergência justificada; hash de fixture idêntico.
- Unitários direcionados: `tests/test_writing_parity_evaluation.py`, `test_rag_deterministic_validation.py`, `test_summary_contract.py`, `test_summary_output.py`, `test_anthropic_contract.py`, `test_rag_temporal_facts.py` — **45 passed**.
- Integrações PostgreSQL diretamente afetadas: `test_legal_knowledge_parity_postgres.py`, `test_pipeline_e2e.py`, `test_pipeline_vector_e2e.py`, `test_pipeline_resilience_e2e.py`, `test_judit_paginated_source_e2e.py`, `test_invalid_summary_publication.py`, `test_summary_provenance_postgres.py`, `test_public_summary_lifecycle.py`, `test_parser_parity_postgres.py`, `test_legal_delivery_acceptance.py`, `test_validation_persistence_postgres.py`, `test_local_demo_seed_postgres.py` e `test_summary_existing_valid_fast_path_postgres.py` — **22 passed** em invocações direcionadas; M03 foi executado novamente (**1 passed**) para deixar seu processo sintético persistido durante o browser smoke.
- Browser smoke com API real local + documento validado — **PASS (synthetic)**; API/clipboard preservam conteúdo e datas (normalização somente de newline de plataforma).
- `node tests/frontend_behavior_test.mjs` — PASS; `python -m ruff check app scripts tests` — PASS; `python scripts/project_harness.py` — PASS (26 guardrail/wiring observations); `gitleaks detect --no-git --source . --redact` — zero leaks; `git diff --check` — PASS.
- `python -m scripts.evaluate_synthetic_rag --check-baseline` — PASS (30 fixtures, baseline synthetic-30-v1; scope limitado a métricas/contratos codificados nas fixtures).
- Os checks locais foram executados na árvore desta branch, baseada inicialmente em `33a71c33a93438cd296e783e33bddfbc11b42b17`. A execução completa de CI no GitHub — incluindo unitários, harness, avaliações offline, browser smoke, imagem, backup/restore, todas as integrações PostgreSQL sem skips e release smoke — passou para `d4a8205b0f08eab62291e7c974e8dfa58848b8c6` (run `40057106691`).

Nenhuma chamada paga, processo real, merge, release ou promoção à `main` foi executado. O resultado é evidência sintética de contrato, cobertura e integração; não mede qualidade de modelo real, latência, custo ou comportamento de produção.
