# M03 — Paridade de redação jurídica

## Objetivo e proveniência

M03 adapta os contratos textuais úteis do RagResumo ao único pipeline de geração do RAgvia. A referência do doador é `oigorbrito/rag_resumo` no commit `0d0f8cbb495292a3f3d61fba75428f3136059ea8`; o destino parte da PR #14, branch `work/ragvia-m02-knowledge-parity`, que contém o commit M02 `363cbc286d20bb3dc7014b185c68823b7e1db62f`.

Falha visada: a redação pode não expressar com clareza a contextualização em dois parágrafos, a cronologia, a separação entre data do ato e data agendada, o agrupamento de expediente e os limites de explicação jurídica. Invariantes: fonte rastreável por claim, saída estruturada validável, sigilo/tenant, minimização de dados pessoais e renderer determinístico.

## Migração e limites

| Requisito do doador | Decisão | Estado / justificativa |
|---|---|---|
| Introdução contextual em dois parágrafos | DIVERGÊNCIA JUSTIFICADA | `synthesis` é um único campo de texto corrido e o parser normaliza quebras; M03 pede duas partes semânticas (contexto e andamento), sem introduzir mudança de formatação/schema. Cabeçalho e listas continuam determinísticos e não são repetidos pelo provider. |
| Papel de cada parte e representante relacionado | ADAPT | A aplicação rotula deterministicamente o polo apenas quando cadastrado (`polo cadastrado: ativo/passivo`) e só exibe `represents` se houver vínculo registrado; não transforma polo em mérito, responsabilidade ou relação inferida. |
| Explicações de classe e assuntos | JÁ EQUIVALENTE | O contrato M02 permite definições TPU oficiais e complemento jurídico recuperado e validado; sem match, não se usa conhecimento externo. M03 reforça linguagem simples sem afrouxar a fonte. |
| Linha do tempo, atos repetidos e marcos | ADAPT | Timeline ordenada e agrupamento sem perda de marcos; movimentos do app continuam fonte e claims continuam obrigatórios. |
| Data do ato versus data/horário agendado | JÁ EQUIVALENTE + REFORÇADO | As anotações temporais e regras M02 já distinguem as datas; prompt reforçado contra tratar evento agendado como ocorrido. |
| Português, neutralidade, fatos versus alegações e ausência de dados | JÁ EQUIVALENTE + REFORÇADO | Regras de incerteza e proibição de prognóstico já existiam; composição agora explicita linguagem acessível e declaração de lacunas. |
| `tool_use` e schema próprio do doador | DIVERGÊNCIA JUSTIFICADA | Não portar. Preservados JSON Schema/validador do RAgvia, schema-v3 e leitura compatível com schema-v2. |
| HTML livre, renderer do doador e `completar` por template | DIVERGÊNCIA JUSTIFICADA | Não portar. Provider só devolve texto estruturado; renderer e gate de publicação canônicos permanecem. |
| Execução de referência em fixture sintética | EXECUTADA; COMPARAÇÃO PENDENTE | `python -m pytest -q tests/test_resumo.py` no worktree doador fixado: 11 passed, incluindo fixture sintética `tests/fixtures/judit_exemplo.json` e teste de exclusão de documentos da mensagem. Não foi feita comparação automática do mesmo texto de saída entre sistemas. |
| Demonstração integrada Judit→API→frontend e igualdade do documento | EXECUTADA PARCIALMENTE | `test_legal_knowledge_parity_postgres.py` em PostgreSQL 16/pgvector descartável, com fixture sintética M01 e cliente Anthropic stub: 1 passed; o arquivo `docs/evaluation/evidence/m03-synthetic-summary.md` foi comparado byte/textualmente com `iaSummary` e `summary.markdown` (2.685 caracteres, igualdade exata). O teste frontend geral passou; não constitui browser smoke do documento específico. |

## Classificação de arquivos do doador

- `resumo/prompt.py`: **ADAPT** — contratos de linguagem, composição, cronologia e agrupamento, sujeitos às regras mais restritivas do RAgvia.
- `resumo/generator.py`: **REFERENCE_ONLY** — identificar as intenções de redação; não copiar cliente Anthropic, schema `tool_use`, template ou `completar`.
- `resumo/render.py`: **REFERENCE_ONLY** — entender a separação entre texto do modelo e estrutura determinística; não copiar HTML/markup.
- Testes/fixtures: **REFERENCE_ONLY** — usar somente casos explicitamente sintéticos para comparar cobertura; excluir documentos pessoais dos payloads externos.

## Contrato canônico preservado

O ponto único de geração segue em `app/rag.py`, com `PROCESS_SUMMARY_SYSTEM_PROMPT`, `SUMMARY_OUTPUT_SCHEMA`, `parse_structured_summary`, validação de claims/proveniência, persistência existente e `render_structured_summary`. Não se cria cliente, gerador, renderer ou caminho alternativo. A saída não aceita HTML livre; publicação continua condicionada ao validador e ao ciclo canônico.

## Evidência registrada e lacunas

Base executada: branch `work/ragvia-m03-writing-parity`, base `e2a5bb28eca78699afebd6483f7c41f60550d0ee` (descendente do commit M02 fixado), alterações de trabalho não commitadas no momento da execução; Python 3.12. A fixture e o cliente são sintéticos; não há inferência sobre qualidade de Claude/live.

- Doador fixado: `cd ../ragresumo-m03-reference && python -m pytest -q tests/test_resumo.py` — 11 passed.
- Candidato: `python -m pytest -q tests/test_summary_contract.py tests/test_summary_output.py tests/test_rag_deterministic_validation.py` — 30 passed, incluindo o caso de polo explícito.
- PostgreSQL descartável `pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`: `test_pipeline_e2e.py test_legal_knowledge_parity_postgres.py test_summary_provenance_postgres.py test_invalid_summary_publication.py` — 5 passed.
- `node tests/frontend_behavior_test.mjs` — PASS; `python -m ruff check app scripts tests` — PASS; `python scripts/project_harness.py` — PASS; `gitleaks detect --no-git --source . --redact` — nenhum leak.
- Demonstração API do fluxo M01/M02 e persistência usa provider stub; igualdade exata entre Markdown salvo e `iaSummary`/`summary.markdown` da resposta API conferida. Custos externos: zero.

Lacunas antes de afirmar paridade textual completa: executar comparação de cobertura lado a lado sobre exatamente o mesmo fixture no template doador e no prompt/gerador simulado canônico; comprovar cenários de informação insuficiente e agrupamento de marcos no conteúdo produzido; executar browser smoke específico com o documento M03. Os testes existentes demonstram contratos e integração, não avaliação semântica live.
