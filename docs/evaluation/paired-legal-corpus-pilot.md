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

    mkdir -m 700 -p /tmp/coordenador-ragvia
    python scripts/blind_paired_legal_review.py prepare --case-id m01-doc01-scheduled-hearing --candidate /tmp/ragvia-paired-corpus/summary.md --commit HEAD_EXATO --out /tmp/pacote-revisores-ragvia --private-mapping /tmp/coordenador-ragvia/PRIVATE-mapping.json

O comando cria **somente** A.md, B.md, case-evidence.json e review-form.json
na pasta compartilhável dos revisores; o mapeamento privado fica em diretório
**separado, previamente criado e restrito ao coordenador**. No POSIX, o diretório
exige 0700 e os arquivos são criados em 0600; a gravação binária garante
os mesmos bytes UTF-8, sem conversão LF/CRLF, no Linux e Windows.
No Windows, permissões POSIX não substituem ACLs: use um diretório NTFS
privado do usuário (preferencialmente sob %LOCALAPPDATA%), confira as ACLs
e não publique os materiais ou dados reais sem controle de acesso.
Entregar aos dois avaliadores apenas os quatro arquivos do pacote público;
nunca entregar PRIVATE-mapping.json.
Não executar com documentos reais em infraestrutura pública/compartilhada.

Cada avaliador preenche scores para ambos os lados A e B em cada uma das
quatro dimensões, usando 0 (falha crítica), 1 (falha material),
2 (aceitável com ressalvas) ou 3 (adequado). Identificadores reviewer_id
devem ser distintos; registrar justificativas e evidências separadamente
sob os controles de privacidade do projeto.

Consolidar as notas no ambiente privado:

    python scripts/blind_paired_legal_review.py aggregate --mapping /tmp/coordenador-ragvia/PRIVATE-mapping.json --answers /tmp/pacote-revisores-ragvia/review-form.json --source-evidence /tmp/pacote-revisores-ragvia/case-evidence.json --document-a /tmp/pacote-revisores-ragvia/A.md --document-b /tmp/pacote-revisores-ragvia/B.md --output /tmp/coordenador-ragvia/resultado.json

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

O workflow do doador é opcional e **não dispara em Ready for review**:
o gatilho foi removido na correção da PR #20 (`e865670d`).
Somente uma futura execução manual `workflow_dispatch`, com credencial
de leitura previamente configurada, poderá atestar o doador remotamente.
Isso não é pré-requisito para a CI funcional ou o merge do RAgvia.

## Security qualification of the donor attestation gate

Donor source is PRIVATE. Read permission was authorized in the chat, but
that is NOT an Actions credential. Use a fine-grained, time-limited token
with Contents: Read only on oigorbrito/rag_resumo. Store it exclusively
as RAG_RESUMO_READ_TOKEN in the protected donor-attestation environment
of oigorbrito/RAgvia. Configure required reviewers and restrict
deployment branches/tags before entering the token. No repository-wide
token and no token as workflow input.

The donor workflow is manual-only (`workflow_dispatch`) after the
PR #20 fix; it does not trigger from PR state transitions or synchronize
pushes. The donor SHA remains pinned. Independent remote donor reexecution
is optional, not a RAgvia functional CI dependency. A future protected
environment with a credential should require independent approval.

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

## RAGVIA-REVIEW-03 — evidência e integridade da revisão cega

O pacote privado agora inclui o arquivo `case-evidence.json`, que contém os
fatos **sintéticos e congelados** do parser. Seu SHA-256 está registrado em
`PRIVATE-mapping.json` e em `review-form.json`. O coordenador deve entregar
ao revisor somente `A.md`, `B.md`, `case-evidence.json` e `review-form.json`.
O mapeamento PRIVATE permanece fora dos materiais fornecidos aos revisores.

Cada avaliação contém notas nas quatro dimensões para A e B, acompanhadas
por `annotations` para cada par documento × dimensão. São obrigatórios:

- `justification`: fundamentação textual da nota;
- `document_location`: local do trecho avaliado no documento;
- `source_refs`: lista de referências examinadas, por exemplo `movimento:5`.

Na consolidação, o agregador relê os bytes de case-evidence.json, verifica SHA-256,
identidade do caso, escopo sintético e hash da fixture congelada.
O agregador exige dois IDs distintos, estrutura completa, notas 0–3
inteiras, anotações não vazias e digest do pacote correspondente. Rejeita
notas incompletas, justificativas em branco, localização ausente,
referências duplicadas, atributos extras e digest divergente. Sua saída
somente contém hashes, totais e diferenças pareadas — não nomes dos
avaliadores, anotações, textos dos documentos ou dados Judit.

**Limitações não negociáveis:** IDs distintos não comprovam a independência
real dos avaliadores; referências digitadas não comprovam sua pertinência
ou veracidade. O pacote atual usa fonte e candidato sintéticos, portanto
não atesta geração Claude real ou qualidade jurídica. O coordenador precisa
verificar a identidade, a independência, as fontes e a adjudicação
externamente. As flags `reviewer_identity_independence_verified`,
`source_reference_truth_independently_verified`,
`candidate_generation_verified_live`, `legal_quality_qualified` e
`release_qualified` permanecem falsas. Um par não permite inferência
populacional ou fixação retroativa de limiares. Sem chamadas pagas,
secret provisioning ou merge nesta etapa.

## RAGVIA-REVIEW-04 — imutabilidade dos textos e resolução de referências

Na preparação do pacote A/B, o candidato é recusado se o teste
determinístico de escrita não passar ou se houver sinal estreito de
afirmação factual indevida. Essa triagem é sintética, não certificação
de que o texto foi produzido pelo Claude real.

O mapeamento privado fixa hashes SHA-256 para os **dois documentos** A.md
e B.md. A consolidação recebe explicitamente ambos os arquivos e recusa
ausência, substituição, truncamento ou modificação dos bytes que foram
entregues ao revisor. Os hashes de cada lado permanecem privados para
reduzir o risco de identificação por comparação com o oráculo publicado.

As anotações usam `document_location` no formato `linha:N` ou
`linha:N-M`, contado no documento cego correspondente. A consolidação
rejeita intervalos inexistentes, invertidos ou acima de dez linhas.
A validação apenas confirma que o local existe; não valida se o trecho
justifica juridicamente a avaliação.

Os `source_refs` também devem resolver contra identificadores existentes
em case-evidence.json: `movimento:N`, `parte:ID`, `classe:ID`,
`assunto:ID` ou `campo:KEY` (campo escalar congelado). Referências
inexistentes, inclusive `movimento:999`, impedem a consolidação.
Isso prova apenas a existência do identificador, não a adequação da
fonte à conclusão. A avaliação humana e a adjudicação permanecem
estritamente externas; nenhum reviewer real foi inventado.

O resultado agregado informa `blinded_document_integrity_checked`,
`source_reference_ids_resolved` e `document_location_bounds_checked`,
mas preserva `source_reference_truth_independently_verified=false`,
`reviewer_identity_independence_verified=false`,
`candidate_generation_verified_live=false`,
`legal_quality_qualified=false` e `release_qualified=false`.

Escopo: contrato de revisão do caso sintético congelado, sem chamadas
pagas, sem alterações no workflow do doador e sem merge.


## RAGVIA-REVIEW-04B — pacote de revisores separado e bytes estáveis

A função `prepare` e sua CLI exigem `private_mapping`/`--private-mapping`
**fora** de `--out`. A preparação recusa um caminho de mapeamento situado
dentro da pasta de entrega ou sobrescrita de mapeamento preexistente,
antes de criar o pacote. Um ZIP completo de `--out` não inclui o
de-para privado. O coordenador deve distribuir somente `--out`.

Todos os arquivos passam a ser gravados como bytes UTF-8 explícitos.
Os SHA-256 de A.md, B.md e case-evidence.json são calculados sobre
exatamente os mesmos bytes escritos em disco. Isso evita diferença
LF/CRLF no Windows; testes direcionados são executados na CI Linux
e Windows. Restrições NTFS dependem da ACL efetiva do diretório
escolhido pelo operador, que **não** é automaticamente certificada
pelo script. O piloto segue exclusivamente sintético e sem inferência
jurídica ou liberação de produção.
