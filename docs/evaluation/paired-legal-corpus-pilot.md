# RAgvia — corpus jurídico pareado, piloto 01

## Escopo observado e população

**Um par autêntico**, não trinta casos: a fixture sintética Judit
`tests/fixtures/judit/parser_parity_synthetic.json` alimentou o gerador
template **original** do doador `oigorbrito/rag_resumo@0d0f8cbb...`.
A captura congelada está em `docs/evaluation/evidence/m03-donor-template.json`.
O candidato é o `iaSummary` obtido da API após worker + PostgreSQL no
**HEAD em teste**, nunca o Markdown arquivado em M03. O manifest
`tests/eval/paired_legal_corpus_v1.json` fixa identidade e hashes.
Qualquer alteração dessas fontes invalida o par antes da comparação.

Os 30 registros de `tests/eval/synthetic_cases.json` são casos de
**retrieval/geração simulada**, não pares independentes de escrita do
doador. As mutações da suíte avaliam sensibilidade do medidor e também
**não** aumentam a quantidade de pares.

## Quatro dimensões e critérios de julgamento

| Dimensão | Critério de anotação por especialista |
| --- | --- |
| Fidelidade factual | Partes, polos, datas e atos expressos, sem converter agendamento em realização |
| Omissões materiais | Movimentações e fatos relevantes ao estado do processo, sem apagar efeitos processuais |
| Fundamentação jurídica | Cada conclusão material acompanhada por fonte pertinente e vínculo justificável |
| Legibilidade | Texto utilizável, claro e organizado, sem inferir mérito |

Para cada dimensão, avaliação humana **cega e pareada** (doador/candidato
em ordem aleatória): 0 = falha grave; 1 = falha material; 2 = aceitável com
ressalva; 3 = adequado. Registrar justificativa, localização do problema
e referências. Pelo menos dois avaliadores independentes por caso; resolver
discordância em adjudicação separada, mantendo notas originais.
Essas avaliações **não ocorreram**: `human_review.*.score_0_to_3=null`.
Não calcular preferência, significância ou taxa de erro de modelo com
um único caso e sem anotadores.

## Gate reproduzível e o que ele mede

`scripts/evaluate_paired_legal_corpus.py`:
- valida o commit do doador, SHA-256 da entrada e hash Git do oráculo;
- executa os 13 requisitos fixados da matriz M03 no texto **publicado**;
- associa cada requisito a uma dimensão, distinguindo `COVERED`,
  `FAIL_DESTINATION_OMISSION` e `DIVERGENCE_JUSTIFIED`;
- registra metadados de verificação da aplicação e existência de refs,
  **sem** alegar entailment/verdade jurídica automática;
- sinaliza apenas dois padrões estreitos de risco e registra hashes das
  saídas, nunca textos ou identificadores/credenciais reais.

O teste PostgreSQL integra o medidor ao mesmo resultado da API e
exige preservação determinística. A matriz completa de escrita pode
mostrar omissões mesmo com CI verde: provedor simulado **não valida**
qualidade humana. `legal_quality_qualified` e `release_qualified`
permanecem falsos. O arquivo publicado no artefato CI contém somente
metadados, sem `summary.md` ou payload Judit.

Executar com PostgreSQL descartável:

```bash
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rpy_test
export RAGV_M01_ARTIFACT_DIR=/tmp/ragvia-paired-corpus
python -m pytest -q tests/integration/test_legal_knowledge_parity_postgres.py
python scripts/evaluate_paired_legal_corpus.py \
  --case-id m01-doc01-scheduled-hearing \
  --destination /tmp/ragvia-paired-corpus/summary.md \
  --output /tmp/ragvia-paired-corpus/paired-legal-case-cli.json
```

## Fechamento e limites

Uma afirmação de paridade **real** exigirá uma população previamente
definida de entradas autorizadas, saídas pareadas do mesmo processo,
configuração fixa de modelo/prompt, fonte verificável, rubricadores
independentes, custo e latência observados. Evitar usar o dataset de
retrieval como se fossem 30 resumos escritos pelo doador. O corpus piloto
fecha apenas a infraestrutura de medição e o caso sintético referenciado,
não as autorizações externas, direito de reutilização ou aceitação jurídica.
Sem merge ou chamadas ao Claude/Judit reais.

## Fechamento: revisão cega A/B, dados locais

Depois de gerar o iaSummary do HEAD atual em PostgreSQL, executar localmente:

    python scripts/blind_paired_legal_review.py prepare --case-id m01-doc01-scheduled-hearing --candidate /tmp/ragvia-paired-corpus/summary.md --commit HEAD_EXATO --out /tmp/pareamento-juridico-privado

O comando cria A.md, B.md, review-form.json e PRIVATE-mapping.json com
permissão restrita (0700 para a pasta, 0600 para os arquivos).
Entregar aos dois avaliadores apenas A.md, B.md e review-form.json.
Nunca divulgar PRIVATE-mapping.json antes da conclusão da avaliação.
Não executar com documentos reais em infraestrutura pública/compartilhada.

Cada avaliador preenche scores para ambos os lados A e B em cada uma das
quatro dimensões, usando 0 (falha crítica), 1 (falha material),
2 (aceitável com ressalvas) ou 3 (adequado). Identificadores reviewer_id
devem ser distintos; registrar justificativas e evidências separadamente
sob os controles de privacidade do projeto.

Consolidar as notas no ambiente privado:

    python scripts/blind_paired_legal_review.py aggregate --mapping /tmp/pareamento-juridico-privado/PRIVATE-mapping.json --answers /tmp/pareamento-juridico-privado/review-form.json --output /tmp/pareamento-juridico-privado/resultado.json

O consolidado mostra as diferenças pareadas e a concordância de direção.
Por definição, um único par não suporta inferência populacional:
legal_quality_qualified e release_qualified permanecem false.

A CI também reproduz a captura do doador a partir do commit Git original
usando scripts/verify_donor_reference.py. Compara seções, documento e
proveniência completos, mas publica só hashes. Nenhum código do doador
é transportado para o runtime do RAgvia e nenhuma chamada paga é feita.


## Acesso ao doador privado — gate separado

O repositorio `oigorbrito/rag_resumo` e privado. A CI padrao do RAgvia usa
um GITHUB_TOKEN restrito ao seu repositorio; por isso a tentativa de checkout
do doador nas CIs #56/#57 falhou com Repository not found. Nao se trata de
falha do gerador e nao existe evidencia de reproducao externa nesse teste.

A workflow manual `donor-reference-reproduction.yml` so executa apos o
dono provisionar no repositorio RAgvia a credencial de leitura
`RAG_RESUMO_READ_TOKEN` com permissao **Contents: Read** exclusivamente
sobre o repositorio privado do doador. Nao colocar token em input, logs,
commit nem variaveis nao secretas. A workflow usa o commit congelado,
verifica a saida original e publica apenas hashes. A CI padrao segue
independente, e **reproducao externa do doador = PENDENTE** ate haver
sucesso em uma execucao manual desse gate.

A verificacao pre-merge pode ser acionada pela transicao manual da PR
de Draft para Ready for review (evento ready_for_review). Enquanto Draft,
a verificacao nao executa, preservando o gate de autorizacao. O dono deve
provisionar primeiro o secret de acesso somente leitura. Se nao houver
secret, a verificacao falha de maneira explicita e nao afeta a CI
funcional independente. Uma execucao workflow_dispatch tambem estara
disponivel quando a workflow existir na branch padrao.

## Security qualification of the donor attestation gate

Donor source is PRIVATE. Read permission was authorized in the chat, but
that is NOT an Actions credential. Use a fine-grained, time-limited token
with Contents: Read only on oigorbrito/rag_resumo. Store it exclusively
as RAG_RESUMO_READ_TOKEN in the protected donor-attestation environment
of oigorbrito/RAgvia. Configure required reviewers and restrict
deployment branches/tags before entering the token. No repository-wide
token and no token as workflow input.

The donor workflow triggers on owner-initiated ready_for_review of
consolidated PR #20 against migration/ragvia-01-candidate (or explicit
manual workflow_dispatch once installed on the default branch).
A synchronize push never exposes the secret. The workflow pins the
candidate head commit, not a generated merge ref; the donor SHA is
pinned separately. No PR Draft or merge change is performed by this
implementation. An environment with no required reviewers is NOT
adequately protected, even if it has a benign-sounding name.

The verifier is invoked using python -m scripts.verify_donor_reference
so Python resolves imports from the project root without assuming an
editable install. All outputs are hash-only. Donor reexecution stays
PENDING until the actual gated job completes successfully.


## Qualificação estrita de equivalência textual do piloto

Para a rubrica sintética, o avaliador aceita uma diferença editorial
verificável: a citação da classe pode começar por `Art. 3º` ou `3º`,
desde que **todo** o trecho substantivo congelado da classe e o trecho
do assunto estejam presentes. Alterações no conteúdo jurídico permanecem
`FAIL_DESTINATION_OMISSION`.

A data de designação e a data/horário agendados devem coexistir **no mesmo
movimento de audiência designada**, com indicação explícita de agendamento.
Datas isoladas, designação negada e texto que descreva a audiência como
realizada não recebem cobertura. Isso não é inferência jurídica nem
reconhecimento semântico aberto: são variantes restritas e testadas por
mutações adversariais.

A cobertura desses dois requisitos não dispensa a avaliação de agrupamento
administrativo nem a declaração de informação ausente. O número de pares
autênticos continua sendo um. A aprovação da rubrica textual jamais altera
`legal_quality_qualified=false` ou `release_qualified=false`.
