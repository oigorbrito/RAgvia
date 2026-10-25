# RAGVIA-M04-F2 — Reconciliação da verificação de proposições

## Resultado

A proteção factual foi mantida e as regressões sintéticas foram corrigidas. A mudança continua local à branch `work/ragvia-m04f-claim-proposition-verification`; não houve chamada paga, processo real, migração de banco ou alteração de schema v2/v3.

Base funcional: commit `980a967b4589fd58ac25697b4401a5921ef7dfdd` (PR #17). A árvore final validada para unit/integração e smoke offline é o commit `72c7deeebad37c0b3ebf9ab398b8764f1ecfc14a` (follow-up sobre o verificador). Os testes PostgreSQL usaram exclusivamente o executor descartável e fixtures sintéticas.

## Diagnóstico único

A falha de maior consequência não era uma falha na validação textual nem na gravação de uma síntese rejeitada. A decomposição nova podia concluir que **todas as proposições** de uma claim tinham suporte — algumas por texto exato e outras por fatos determinísticos distribuídos entre fontes — mas deixava as relações claim-fonte como `not_evaluated`: a relação antiga avaliava a claim inteira contra uma única fonte. O publicador exige ao menos uma relação avaliada para uma claim avaliada (`claim_evidence_is_publishable`). Assim, a validação persistida podia dizer `passed: true` e a API ainda ocultar o resumo válido.

Isso foi observado diretamente no caminho PostgreSQL/API: `claim_evidence_is_complete` e a comparação do documento estruturado com o processo eram verdadeiras; a claim e suas proposições estavam `supported`; as fontes contribuintes permaneciam `not_evaluated`; o publicador então recusava o resumo. O primeiro decisor incorreto era, portanto, a relação claim-fonte não reconciliada com a evidência por proposição, não o renderer nem a leitura histórica.

A reexecução inicial dos oito node IDs selecionados registrou **6 falhas e 2 aprovações** — não cinco. A discrepância de contagem é registrada explicitamente. Quatro falhas de geração/publicação desapareceram ao reconciliar relações e canonicalização abaixo; a falha do caso de conhecimento jurídico restante era um fixture de geração sintética que produzia paráfrases/extrapolações sem suporte próprio. O parser de schema também colapsa quebras de linha; um fixture que injetava diretamente Markdown com quebras bypassava a normalização e produzia documento não canônico. Esses dois pontos foram corrigidos no nível dos fixtures, sem relaxar o verificador nem alterar o pipeline de produção.

Evidência do diagnóstico inicial: `C:/Temp/ragvia-m04f2-eight-diagnostics/postgres.log` e `C:/Temp/ragvia-m04f2-paginated-diag3/postgres.log`. O segundo contém o estado exato das claims/fontes e os resultados de canonicalização antes da correção final. Revisão adicional acrescentou teste que garante que uma proposição contradita não apaga proposição separada suportada.

## Correção mínima

Em `app/claim_verification.py`:

- Cada proposição conserva seu próprio status, motivo e referências de suporte. Uma claim só é `supported` quando todas as suas proposições o são; contradição tem precedência, seguida de insuficiência e não avaliação. Portanto, uma proposição legal sem suporte não é mascarada por outra proposição comprovada, e uma proposição contradita não apaga o status/referências de uma proposição separada que é suportada.
- Uma relação fonte é marcada como suporte apenas se essa fonte contribui para uma proposição comprovada. Relações contraditórias nunca são promovidas. O motivo persistido explicita que o suporte é à proposição, e o resumo ainda depende da aprovação de todas as proposições.
- A decomposição reconhece o rótulo explícito `Andamento processual:` mesmo quando o parser normaliza whitespace. Correspondências textuais continuam exigindo limites de proposição; não foi introduzida correspondência por substring livre.
- Mensagens de insuficiência incluem motivos estáveis e índices de proposição, sem persistir o texto da claim dentro do erro.

A persistência existente registra motivo, hash do excerto e localização da relação já disponível, permitindo que o publicador aplique o contrato existente. Não houve mudança de SQL/schema, API, prompt, retrieval ou renderer. Os fixtures sintéticos agora mantêm o documento estruturado canônico e usam texto-fonte explícito onde a evidência anterior era apenas uma paráfrase não validada.

## Comparação antes/depois

- **Antes:** uma síntese com fatos determinísticos podia ser aprovada embora contivesse uma explicação jurídica adicional sem suporte; o teste adversarial original foi reproduzido contra o verificador anterior e falhou. A decomposição inicial da correção também deixava fontes exatas como `not_evaluated`, causando resumos validados mas não publicáveis.
- **Depois:** explicação jurídica sem suporte próprio é insuficiente/não avaliada e bloqueia publicação mesmo ao lado de fatos suportados. As proposições comprovadas mantêm suas referências; suas relações são persistidas como suporte. Texto que não pode ser verificado não é rotulado como falso, mas não é publicado como fato.
- As variações sintéticas cobrem procedência legal inventada, trânsito em julgado, revelia, cancelamento/manutenção de audiência, prazo, evento posterior contraditório e ausência de informação. O caminho canônico ainda gera, persiste e recupera resumo válido pela API; candidato inválido não substitui resumo anterior válido.

## Falha e correção do smoke de release offline

A CI inicial do commit `f3949eb3eb138544c74adf60eeba8d3f88429d90` falhou no smoke offline de release (`scripts/smoke_offline.py`), antes da etapa final. O fixture montava status/atenção narrativos, citava apenas a fonte do processo e o stub não aceitava a correção. O verificador corretamente não publicou esse resumo: essas proposições não tinham suporte textual nem referência a movimento. Foi corrigido apenas o fixture: movimentos sintéticos agora contêm as frases exatas usadas no resumo; claims relevantes citam essas referências. O smoke registra diagnósticos de estado/validação para falhas e passou localmente em `bash scripts/smoke_offline.sh`, com PostgreSQL descartável e zero chamadas a provider. O erro não foi corrigido relaxando aceitação.

## Limites

Este verificador é uma salvaguarda determinística de referências, correspondência textual e fatos estruturados, não um provador de entailment jurídico geral. Paráfrases e explicações livres não cobertas por esses sinais podem permanecer não avaliadas/insuficientes e requerer omissão ou correção; não se reivindica robustez semântica geral. A aceitação dos fixtures é sintética, não demonstra qualidade de modelo/provedor real, latência ou custo.

## Qualificação final

- Os oito testes PostgreSQL canônicos requeridos foram incluídos na execução final mais ampla: **12 passaram, 0 falhas/erros/skips** nos sete arquivos `test_api_v1_postgres`, `test_judit_paginated_source_e2e`, `test_legal_knowledge_parity_postgres`, `test_parser_parity_postgres`, `test_pipeline_e2e`, `test_pipeline_resilience_e2e` e `test_public_summary_lifecycle`. Evidência: `C:/Temp/ragvia-m04f2-final3-canonical/`. Imagem PostgreSQL `pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`; `run.json` conserva comando e ambiente.
- Seis testes de persistência/publicação: **6 passaram, 0 falhas/erros/skips**, `C:/Temp/ragvia-m04f2-final3-persistence/`. Incluem resumo anterior válido, regeneração de inválido/incompleto, caminho rápido válido e documento restrito.
- Suíte unitária completa em Linux: **1.377 passaram**, Python 3.12.14, pytest 9.0.3, imagem local de teste `sha256:c0f369a87fe34768297e6058d968f316427bbfa405771defd8b2c641134d24a1`; plugins `pytest-asyncio`, `anyio` e Hypothesis foram carregados explicitamente porque a imagem não auto-carrega os entry points. Comando equivalente à CI, com plugins explícitos: `pytest -p pytest_asyncio.plugin -p anyio.pytest_plugin -p hypothesis.extra.pytestplugin -q tests --ignore=tests/integration --hypothesis-seed=20260918`.
- Testes direcionados exigidos: **67 passaram** (`test_claim_verification`, `test_claim_evidence`, `test_validation`), incluindo a regressão que preserva proposição suportada ao lado de proposição contradita.
- `bash scripts/smoke_offline.sh` passou com PostgreSQL descartável; o provider foi bloqueado pelo fixture, sem chamadas.
- `ruff check app scripts tests`, `python scripts/project_harness.py`, `git diff --check` e Gitleaks passaram na árvore local final.
- A CI do commit inicial `f3949eb` falhou no smoke offline por fixture sintético sem referências de suporte; o diagnóstico e ajuste estão acima. Após a correção, a CI passou para o head `28a1361e267c7feb2130f46b30f1e754c28f0795`, run `40118512349` (todas as etapas, incluindo integração PostgreSQL sem skips e release smoke offline). As suítes unitária, PostgreSQL, targeted e offline smoke também passaram localmente no código do commit `72c7deeebad37c0b3ebf9ab398b8764f1ecfc14a`, pai direto desse commit de documentação; os `run.json` finais identificam o commit de código executado.
