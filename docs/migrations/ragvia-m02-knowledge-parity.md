# RAGVIA-M02 — recuperação jurídica com paridade e proveniência

Base obrigatória: M01 `1a2e53126343a514680af4d85d3a7cfebe97028f` (PR #13).
Doador fixado: `oigorbrito/rag_resumo` em `0d0f8cbb495292a3f3d61fba75428f3136059ea8`.
Branch independente `work/ragvia-m02-knowledge-parity`, empilhada sobre PR #13.

## Objetivo e invariantes antes da implementação

**Falha medida:** o catálogo canônico TPU contém duas definições oficiais por
código e não cobre sinônimos de recuperação, movimentos ou conceitos. Isso pode
deixar o contexto do resumo sem explicação. O conteúdo complementar, porém, não
pode sobrepor CNJ, converter similaridade em fato de processo ou introduzir
proposições jurídicas sem fonte identificável.

Camadas de proteção: resolução oficial CNJ por código/tipo; suplemento distinto
e versionado; seleção depois do short-circuit de sigilo; fallback apenas no BGE
local, quando explicitamente habilitado e indexado no PostgreSQL; hashes de
conteúdo/espaço, proveniência de definição e validação de claims na publicação.
Sem resultado correspondente não há explicação jurídica. Sem índice BGE não há
fallback semântico silencioso. API não tem SELECT na tabela de embeddings; só o
worker indexa/consulta. Classes, assuntos e movimentos continuam campos e fontes
canônicas; nenhum texto de conhecimento altera Judit, histórico ou ordinais.

Contratos impactados: `JUDIT-LEGAL-KNOWLEDGE-001`, `JUDIT-PARSER-PARITY-001`,
`GENERATION-SPEND-001`, `TENANT-001`, sigilo, evidência de claims, proveniência
de sumário e recuperação. A autorização financeira não é contornada: esta mudança
não altera o cliente, permit, tentativas, aprovação ou reserva Anthropic. Não há
merge, release ou promoção à main.

## Inventário restrito e oracle anterior às adaptações

Foram lidos integralmente `resumo/knowledge.py`, `resumo/embeddings.py`, todos os
registros dos arquivos `kb/*.jsonl` (um arquivo, 33 registros), `scripts/indexar.py`,
os testes `tests/test_resumo.py` e sua fixture. O oracle executou o código original
sem edição em namespace privado, usando HashingEmbedder/NumPy originais e também
vetores controlados inteiramente sintéticos. O fixture do pipeline é o fixture
M01 sintético; nenhum dado do exemplo real do doador foi copiado.

O oracle congela seis buscas de item, quatro buscas com tipo/k/min_score,
cinco casos de limites/score/filtro com vetores controlados, precedência e o
contexto M01 (`c1`/`c2`, `a1`/`a2`/`a3`, movimentações e conceitos). SHA dos
arquivos doador, IDs, origem, score, ordem e hashes de texto estão em
[`tests/fixtures/legal_knowledge/donor_oracle.json`](../../tests/fixtures/legal_knowledge/donor_oracle.json).
O fixture guarda hash, não republíca os textos do glossário doador. Os hashes são
determinísticos sobre UTF-8 dos valores originais. Nenhum modelo real é chamado.

| Contrato observável no doador | Implementação escolhida | Diferença deliberada |
|---|---|---|
| tipo+código, depois termo/sinônimo normalizado, depois similaridade | `resolve_process_tpu_definitions` e `resolve_legal_knowledge` em `app/tpu_glossary.py` | Código oficial CNJ sempre precede complemento; matcher lexical sempre respeita tipo |
| NFKD, remoção de acento e espaços | `normalize_legal_term`, mesma normalização M01 | Mantém o comportamento para português, sem importar parser/pipeline doador |
| busca item classe/assunto | lista determinística, dedupe por `id` | Corrige vazamento do doador: alias JEC consultado como assunto retornava verbete de classe |
| movimentos k=2/min 0,25; classes/assuntos min 0,35; conceitos min 0,2 | thresholds/k registrados no resolver; resultado semântico só do índice BGE local | Score de hashing não se compara numericamente ao BGE. Threshold é hipótese/copied, ainda não calibrado em qualidade real |
| classes, assuntos, movimentos e conceitos no contexto | campos `tpu_glossary` e `legal_knowledge` do contexto canônico | Movimentos consultam somente os movimentos já selecionados pelo retrieval canônico; ordenação/força-inclusão M01 permanece |
| deduplicação dos trechos | ID único por contexto e ordem determinística | K aplicado por consulta semântica do movimento; duplicatas entre consultas removidas |
| origem `codigo`, `termo`, `semantica` | `match_method` `code`, `term`, `semantic_bge`; score/contexto e hash da definição | Fonte legislativa e SHA preservados; semantic score apenas ordenação mecânica |
| JSONL, NumPy, `.npy`, HashingEmbedder e SentenceTransformer/E5 | REFERENCE_ONLY para oracle; armazenamento pgvector/espaço BGE já existente | Não introduz NumPy no runtime, pacote, modelo novo, download ou endpoint |
| `scripts/indexar.py` | `scripts/index_legal_knowledge.py` | Exige snapshot/artifact BGE offline verificado e `WORKER_DATABASE_URL`; apenas BGE local, sem Cohere/OpenAI |
| score e empate dependente de `np.argsort`; `k=0` pode devolver item | score cosseno pgvector, ordenação estável por ID, limite positivo | Erros silenciosos/quantidade inválida do doador são rejeitados |
| ausência/código desconhecido | lista vazia para aquela classe/assunto/movimento | Sem correspondência não ocorre preenchimento com conhecimento externo |

`data/tpu/2026-09-12.json` e definições CNJ não são alteradas. O código `7`
continua a definição oficial CNJ com seu `definition_sha256`, publisher e fonte.
Sinônimos `PROCEDIMENTO COMUM`/`AÇÃO ORDINÁRIA` ficam separados em
`supplemental_aliases` do complemento, explicitamente `verified_by_cnj=false`; só
resolvem para um verbete oficial conhecido, sem alterar definição, nome oficial,
versão, publisher ou código recebido. Mapeamentos class/subject do doador não são
apresentados como endossados pelo CNJ.

## Conteúdo: decisão registro por registro

A fonte complementar não republica a prosa explicativa do doador. Cada trecho
transportado foi substituído por excerto curto de norma/ato oficial, ligado por
URL, dispositivo e fonte HTML fixada em
[`data/legal-knowledge/sources.json`](../../data/legal-knowledge/sources.json).
`docs/migrations/evidence/ragvia-m02-content-audit.json` tem decisão, ID original,
hash do texto doador, excerto/URL, aliases mantidos ou removidos e fonte primária.
O JSON runtime é `data/legal-knowledge/2026-10-22.json`, versão distinta do TPU,
com publisher explícito “não é tabela CNJ”; `definition_sha256` é calculado ao
carregar e é o que fica na proveniência.

Resultado dos 33 registros: **26 extratos/entradas adaptados com fonte primária**,
uma definição de procedimento comum reaproveitada exclusivamente do TPU oficial,
e **seis textos jurídicos rejeitados**. Removidos conceitos cuja explicação ampla
não estava sustentada no escopo de fonte primária examinado, efeitos universais
de arquivamento/remessa, regra geral de prazo no DJEN e estado de fase presumido.
Classes e assuntos têm seus vínculos ao código doador rotulados como não oficiais;
`EXECUÇÃO`, embargos de mais de uma espécie, “publicação”, “audiência designada”
genérica e outros aliases ambíguos foram removidos quando poderiam explicar um
registro diferente. Não foi adicionada jurisprudência, doutrina, previsão ou regra
que não esteja no trecho identificado.

Os excertos são suporte informativo, não atualização jurídica certificada. As
páginas-fonte foram capturadas em **22/10/2026**; SHA-256 de cada resposta, URL,
tamanho e modo de decodificação estão registrados. Isso prova identidade do
artefato observado, não vigência, interpretação completa ou aconselhamento. Lei
especial, alteração posterior, regime do processo e exceções podem ser relevantes.
O gerador é instruído a não calcular prazo, aplicar consequência ao caso, transferir
regra entre tipos ou usar definição sem correspondência; a validação não aceita
explicação factual sem apoio textual recuperado.

## Pipeline canônico e proveniência

`app/rag.py::_load_context` mantém o caminho TPU oficial e, após o short-circuit
de sigilo, recupera suplemento determinístico por código/tipo, termo/sinônimo,
classes/assuntos, texto dos movimentos selecionados e conceitos processuais
condicionados à presença de partes, representantes, valor ou classe JEC. O
suplemento fica em `legal_knowledge`, separado de `tpu_glossary`; ambos entram no
payload do gerador existente e nas instruções da aplicação como dados não
confiáveis. Nenhum parser RAG, cliente, endpoint ou renderer paralelo foi criado.

Fallback semântico é avaliado apenas para misses determinísticos, se
`EMBEDDING_SPACE_RUNTIME_ENABLED=true`, o espaço selecionado é BGE local, e existem
vetores no `legal_knowledge_embeddings` para o par exato `(entry_id,
embedding_content_sha256)` do snapshot. `embedding_content_sha256` cobre nome,
aliases e definição; mudar qualquer texto invalida vetores antigos. SQL filtra
BGE/modelo/digest e recupera top-k no mesmo espaço cosine pgvector. Thresholds do
doador são mantidos (`.35` classe/assunto, `.25` movimento, `.2` conceito); nenhum
Cohere/OpenAI, rede ou fallback textual automático é chamado. Corpus faltante,
modelo ausente ou vetor inexistente não produz definição semântica.

O índice é artefato operacional explícito: aplicar migration 036, verificar o
artefato local imutável `BAAI/bge-m3` com `scripts/verify_bge_image_runtime.py`,
forçar variáveis offline e executar `scripts/index_legal_knowledge.py --model-dir …`
com credencial de worker. A indexação faz UPSERT idempotente por conteúdo/espaço;
nenhum download automático. Nenhuma inferência/consulta semântica foi paga ou
executada nesta homologação. Os vetores usados no teste pgvector são sintéticos,
não medem semântica BGE nem qualidade jurídica.

`process_summary_glossary_sources` recebe, na ordem usada, fontes TPU oficiais e
complementares (kind class/subject/movement/concept, código, versão, origem,
source_ref, SHA da definição). `process_summary_glossary_sources` preexistente
continua servindo a API. Migration 036 amplia CHECK de `kind` de forma aditiva e
cria a tabela de vetores isolada; credenciais concedem acesso de embeddings apenas
a `rpy_worker`, sem SELECT à API. A tabela é estática, sem tenant ou texto de
processo. Não foi concedida permissão nova a API, scheduler ou backup.

Para a afirmação gerada, `process_evidence_ref` tem `legal_knowledge` no catálogo
de verificação; claims devem corresponder exatamente ao excerto textual suportado.
A fonte semântica não é tratada como fato do processo: ela explica norma/conceito,
e a fonte de processo continua ligada a movimento/processo. Quando a afirmação
reproduz exatamente uma cláusula legal, o excerpt persistido da verificação é
reduzido à cláusula normalizada correspondente (não truncado ao JSON inteiro);
o teste comprova texto/hash e relação `supported`. A proveniência da norma é
persistida separadamente pelo hash/versionamento e sobrevive à leitura
pública autorizada. Uma alegação sem trecho recuperado não recebe inventário para
ser citada; se houver afirmação material não avaliada/sem evidência, validação
canônica impede publicação. A integration exercita `iaSummary`, verificação
`supported`, IDs/versões/hash e isolamento de tenant. Texto legal e claims não
alteram a autorização/gasto Anthropic, efetivos ou fontes processuais.

Sigilo retorna antes de carregar o suplemento, consultar pgvector ou embeddar.
Embeddings requerem BGE local; provider externo de embeddings não é selecionado
pelo resolver M02. São preservados versão finalizada, retenção, worker/tenant,
permissões e save transacional de summary/fontes. Definições e APIs de uma norma
não são autoridade para interpretar resultado de processo.

## Conteúdo sem resultado e limites

O contexto semântico proposto não licencia invenção: somente IDs pertencentes à
lista versionada e ao tipo requisitado são aceitos. Desconhecido/código inválido
não tem fallback de nome livre, salvo nome/alias exato do mesmo tipo; semantic
fallback requer índice versionado e score mínimo. A seleção é limitada (uma classe,
um item por assunto, dois candidatos por busca de movimento, dedupe por `id`, quatro
conceitos conhecidos); documentos não recebem listas ilimitadas. Match `semantic_bge`
e score são internos de recuperação e não devem ser apresentados como confiança
jurídica.

Divergências empíricas intencionais: o oracle doador, para `(assunto, “JEC”)`,
retorna `classe-436`; o destino retorna zero assuntos, evitando vazamento de tipo.
Para busca semântica `audiência de conciliação` do doador, o segundo resultado é
audiência de instrução; o destino escolhe match de termo determinístico e não
injeta a audiência de instrução. Vetores BGE e Hashing têm espaços/réguas
incomparáveis, não se exige score numérico idêntico. Empates agora são estáveis.

## Testes e evidência

Unitários sintéticos: `tests/test_legal_knowledge.py` cobre oracle/doador fixado,
código e precedência, termos/aliases, tipo, desconhecidos, limiares inclusivos,
k=2, empate estável, dedupe, proveniência/hashes e verificação de claim. `tests/test_tpu_glossary.py`
cobre aliases oficiais sem alterar definição CNJ. Integração
`tests/integration/test_legal_knowledge_parity_postgres.py` percorre Judit JSON →
normalização M01 → recuperação oficial/suplementar → prompt existente → provedor
stub → validator/claim verification → commit PostgreSQL → API `iaSummary`, fontes,
replay e tenant. Vetores sintéticos testam pgvector top-k/filtro/limiar em tabela
durável sem BGE real. Cobertura adjacente roda glossário TPU, papéis/permissões,
retrieval, secrecy, claim, summary provenance, invalid publication e geração
pré-autorizada.

```powershell
python -m pytest -q tests/test_legal_knowledge.py tests/test_tpu_glossary.py tests/test_claim_verification.py
$env:RAGV_M01_ARTIFACT_DIR = "$env:TEMP/ragvia-m02-new/document"
python scripts/validate_candidate_postgres.py --output "$env:TEMP/ragvia-m02-new/postgres" `
  --test-path tests/integration/test_legal_knowledge_parity_postgres.py `
  --test-path tests/integration/test_tpu_glossary_postgres.py `
  --test-path tests/integration/test_summary_provenance_postgres.py `
  --test-path tests/integration/test_claim_evidence_postgres.py
```

Resultado local nesta execução: **137 testes direcionados** (imagem Linux fixa) e
**28 integrações PostgreSQL direcionadas**, zero skips, incluindo o percurso
completo, pgvector sintético, tenant, sigilo, autorização e publicação. Ruff,
project harness e `index_legal_knowledge.py --help` passaram. Gitleaks e CI do head
final serão registrados abaixo depois de publicar o branch. PostgreSQL é o
container descartável do executor, digest e hashes em `qualification.json`, porta
loopback e cleanup confirmado. Fixtures, oracle e vetores de integração são
sintéticos. Não é evidência de compatibilidade real do BGE, qualidade jurídica,
latência, custo ou comportamento Claude.

## Arquivos e reversão

Alterados: `app/tpu_glossary.py`, `app/rag.py`, `app/prompts.py`,
`app/claim_verification.py`, `app/provenance.py`, `scripts/index_legal_knowledge.py`,
`scripts/provision_db_roles.py`, migration `sql/036_legal_knowledge_embeddings.sql`,
`data/legal-knowledge/{2026-10-22.json,sources.json}`, testes de unidade/PostgreSQL,
`NOTICE`, registro e matriz de contratos e este relatório.

Migration é aditiva: tabela própria de vetor com digest/espaço; CHECK ampliado após
preservar os dois tipos antigos. Rollback: desabilitar suplemento e runtime local;
workers antigos ignoram campo `legal_knowledge`; não iniciar versões antigas enquanto
se exigir provenance com kind novo. Backup da tabela vetorial é recriável a partir
do snapshot e do artefato BGE verificado. Proveniência durável permanece ligada
às versões/sumários existentes; rollback não apaga histórico ou claims. Remover
migration/contrainte ou dados exige plano separado; a migration não é revertida
automaticamente.

Licença do snapshot doador não foi encontrada. NOTICE registra origem; nenhum
texto explicativo de baixa fundamentação é transplantado. A classe genérica
“EMBARGOS” foi rejeitada pois o art. 1.022 só fundamenta uma modalidade específica.
Release requer decisão
do proprietário sobre base de uso/redistribuição dos termos e aliases doadores.
Fontes primárias citadas estão identificadas, mas esta migração não certifica
licença de marca/código TPU nem atualidade jurídica.

PR draft permanece empilhada em PR #13; sem merge, release, promoção ou chamada
Claude/Judit/DataJud paga. Qualquer métrica de qualidade jurídica live exige dados
autorizados, protocolo de revisão por gerente/jurista e experimento separado.
