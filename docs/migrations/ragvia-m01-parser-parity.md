# RAGVIA-M01 — paridade determinística do parser

Base destino: `d50b5aedf900aca553be6636fbf3055b15a3da68` (PR #12).
Branch independente: `work/ragvia-m01-parser-parity`.
Doador fixado: `oigorbrito/rag_resumo`, main,
`0d0f8cbb495292a3f3d61fba75428f3136059ea8`.

## Plano de transplante e invariantes

Objetivo: normalizar envelopes/partes/advogados e preservar as classificações,
metadados cadastrais e detalhes lexicais do parser na ingestão existente, sem
transformar heurística em fato publicado ou enfraquecer a fronteira de dados.
Contratos afetados: `API-FASTAPI-001`, `TENANT-001`, `QUEUE-WORKER-001`,
`GENERATION-SPEND-001`, versões finalizadas/proveniência, sigilo e publicação.

| Elementos exatos do doador | Decisão / destino |
|---|---|
| `TRIBUNAIS`, `SITUACOES`, `CLASSES_INCIDENTAIS`, `norm`, `RE_DATAHORA`, `_classificar_movimento` | COPY/ADAPT: regras lexicais em `app/judit_parser_rules.py`, anotações derivadas em metadata, não fatos comprovados |
| `_polo`, `formatar_oab`, `_partes` | ADAPT: identidades canônicas existentes em `app/judit.py`, dedupe normalizado e representação explícita/aninhada; não inferir relação só pelo polo |
| `_digitos`, `mascarar_documento` | REFERENCE_ONLY: manter máscara canônica mais restritiva; não expor os dígitos centrais revelados pelo doador |
| `para_local`, `fmt_data`, `fmt_moeda` | ADAPT/REUSE: timezone/parser e Decimal existentes; formatação de apresentação, sem cálculo de vencimento |
| `_movimentos` | ADAPT: classificação e ordem literal por número textual antes da primeira persistência; IDs e posição de entrada preservados em metadata; ordinais já persistidos nunca são reescritos |
| `_itens`, `_alertas`, `extrair_fatos` | ADAPT: projeção cadastral completa com caminhos de origem e indicadores explicitamente heurísticos; não remover cadastro nem obedecer ao texto “Ignore a área” |
| `extrair_processos` | ADAPT: aceitar `page_data`, item lawsuit, response_data e wrapper lawsuit; nunca ingerir summary externo como fonte |
| `Parte`, `ItemCadastro`, `Movimento`, `Fatos` e `to_dict` | REFERENCE_ONLY: projeções dict nas estruturas canônicas, sem domínio/pipeline paralelo |
| `tests/test_resumo.py` seção parser e fixture | ADAPT: testes equivalentes e oracle em fixture inteiramente sintética; nenhum valor pessoal original |
| generator, RAG, knowledge, prompt, render, CLI/fallback, KB e demais testes do doador | DROP do transplante: só referência de contratos; nenhum cliente/provedor/pipeline importado |

Dependências transitivas da fatia: apenas stdlib (`re`, `unicodedata`, datetime /
zoneinfo já presentes). Nenhuma dependência nova. O snapshot do doador não contém
LICENSE; atribuição/origem devem permanecer registradas. Não atribuir ao doador
uma licença inexistente nem considerar esta migração uma autorização de release.

Parser (329 linhas), testes correspondentes completos e fixture foram lidos;
a fixture inteira foi processada localmente para remover nomes, documentos,
OAB, identificadores e conteúdo externo de resumo antes de virar evidência
versionável. Somente campos consumidos pelo parser são mantidos. O código e
os dados originais não são alterados no checkout privado do doador.

## Divergências que não podem ser ocultadas

- Máscara do doador revela mais dígitos: preservar máscara atual do RAgvia.
- Data naive no doador depende do timezone do host; RAgvia usa UTC explícito.
  Datas inválidas continuam falhando de forma segura na normalização canônica.
- Ordem por número do evento não prova cronologia temporal. A inspeção mostrou
  que o RAgvia ordena/seleciona recência pelo ordinal, não por timestamp: na fixture
  descendente isso invertia primeira/última seleção. Agora números textuais são
  ordenados **antes** de atribuir novos ordinais sequenciais; a ordem literal do
  doador também é preservada em `source_parser.order`. Números explícitos têm
  prioridade sobre inferência textual na ordem canônica. Sem números textuais,
  mantém-se o contrato anterior de ordem de entrada, inclusive com campos
  numéricos explícitos. A integração detectou o caso misto “10   Distribuído”
  (sem hífen) + “20 - ...”: a preferência explícita impede deslocar o evento 10. `source_input_index`, `step_id`, data do
  ato e número literal permanecem rastreáveis. Número 10/20 vira ordinal 1/2,
  não chave durável 10/20. Nenhuma versão finalizada é reordenada.
- “AUDIÊNCIA DESIGNADA”, “CITAÇÃO”, “SENTENÇA” etc. são reconhecimento lexical:
  negação/cancelamento pode tornar a inferência falsa. A anotação literal não
  substitui validação factual nem os controles temporais de negação do RAgvia.
- Última data no texto não é necessariamente data de audiência/disponibilização.
  Preservar essa interpretação literal como anotação, não publicar fato sem fonte.
- `prazo_dias` é duração expressa; jamais somar ao timestamp para inventar vencimento.
- Alertas de classe/área/comarca são hipóteses. Não ignorar campos da fonte nem
  promover “provável erro” a fato, instrução do sistema ou decisão jurídica.

## Interfaces efetivamente transportadas

| Informação do parser doador | Componente canônico / saída |
|---|---|
| page_data / item lawsuit / response_data / JSON puro; wrapper lawsuit | `extract_lawsuit_processes`, `parse_event`, `extract_promotable_fields`; mantém request/response IDs e cache. Webhook exige um lawsuit e IDs estáveis; lote deve ser importado item a item |
| partes, dedupe por nome normalizado, advogados aninhados, OAB e polos | `parties` e `representatives` existentes; relações explícitas por cliente. Projeção plana, IDs locais pN, OAB e caminhos em `header.source_parser.parties/representations` |
| classes/assuntos completos, códigos e IDs cN/aN | `header.source_parser.classifications/subjects`; classe principal e subjects canônicos preservados |
| tribunal extenso, situação/fase, comarca/cidade, moeda e data formatadas, flags digital/citação | `header.source_parser.registry`, com `registry_sources` para cada valor derivado. Valores brutos canônicos não são substituídos por labels |
| classificação dos atos, modalidade/data/horário agendados, destinatário, duração, evento referido e data lexical de diário | `process_steps.metadata.source_parser` com regra/versão/caminho relativo ao lawsuit; não calcula data de vencimento |
| numeração e ordenação | número original em `source_step_number`, posição recebida em `source_input_index`, ID original em `step_id`; novo ordinal sequencial só antes de persistir. O contexto existente recebe também `source_step_number` quando conhecido para não confundir evento com ordinal |
| horário de audiência sem “às” | extensão mínima do extrator temporal existente: reconhece também `dd/mm/aaaa hh:mm`, preservando os controles de negação e cancelamento |
| alertas de inconsistência | `header.source_parser.indicators`, marcados `kind=heuristic`; sem autocorreção, sem republicação como fatos |

O módulo novo contém somente regras puras. Não há `Fatos` paralelo, outro
renderer, pipeline, cliente Anthropic, upload, endpoint novo ou dependência nova.
Os labels de cadastro/moeda/data e a projeção plana de partes ficam disponíveis
no header da API autorizada; não reescrevem o layout/JSON schema-v2/v3 existentes.
Representantes são relações no modelo canônico: o mesmo advogado pode ter duas
linhas para dois clientes, sem virar parte processual duplicada. Compartilhar
polo não basta para inferir cliente. A separação canônica usa os tipos explícitos
já reconhecidos pelo RAgvia, não a substring ampla `ADVOGAD` do doador (por
exemplo, “TESTEMUNHA DO ADVOGADO” não vira representante). A visão literal
permanece na projeção anotada, sem sobrepor esse contrato de identidade.
IDs pN/cN/aN são locais à projeção/version,
não chaves duráveis ou identificadores pessoais.

## Equivalência, privacidade e limites preservados

Já existiam e foram reaproveitados: normalização CNJ, máscara estruturada mais
restritiva, timezone São Paulo/naive UTC, tratamento seguro de datas inválidas,
Decimal para leitura de valores, partes versus representantes, metadados
escalares, fonte/versionamento imutáveis, retrieval e renderer estruturado.
A apresentação monetária replica o float/duas casas do doador para valores
numéricos finitos, mas rejeita valores não finitos/bools e aceita formatos BRL
já suportados por `decimal_amount`; nunca é usada no orçamento financeiro.

Privados são excluídos da derivação (raw source continua sob a retenção existente).
Ao contrário da truthiness literal do doador, strings explícitas de falso não
são tratadas como privadas; valores truthy desconhecidos ficam excluídos.
CPF/CNPJ formatados também são removidos de texto/título e campos derivados;
a máscara canônica continua mostrando só os dois últimos dígitos.
`header.source_parser` e todos os representantes/OAB são excluídos do payload
externo exato. Duração expressa e audiência designada continuam dados mecânicos
da fonte, não prova de ocorrência, vencimento calculado ou orientação jurídica.

A fixture usa somente pessoas/município/órgão/documentos/OAB/IDs sintéticos.
Campos não consumidos e summary externo original foram removidos; um sentinel
sintético comprova que summary não alimenta geração. A conferência local contra
campos pessoais do original não encontrou sobreposição nas novas fixtures/testes.
Uma primeira versão de aliases com sufixos numéricos fez o validador existente
bloquear a publicação por nome truncado no padrão “AUTOR - ...”. Foram usados
aliases alfabéticos e o oracle foi regenerado **pelo doador fixado**; nenhum
validador foi relaxado. Textos com nomes numéricos continuam sujeitos a esse
controle conservador existente, fora do escopo de ampliar o reconhecimento.

## Testes portados e paridade reproduzível

Os quatro contratos de parser de `tests/test_resumo.py` foram adaptados e
expandidos em `tests/test_parser_parity.py`: máscara, fatos básicos/partes,
movimentos/datas e alertas. O oracle JSON foi produzido exclusivamente pelo
parser do SHA doador sobre a fixture nova sintética, sem copiar sua pipeline/KB.

[Evidência do doador](evidence/ragvia-m01-donor-parity.json) registra SHA, hash do
blob, hashes JSON canônicos, três conjuntos de constantes e comparação direta
local de 20 classificações, seis normalizações, quatro polos, seis moedas,
três datas com timezone e do oracle completo. Os testes CI usam esse oracle,
sem depender de checkout/rede do doador. Semântica literal de negação/última data
fica explicitamente incompatível com assumir fatos; testes confirmam que não
chega ao provedor como instrução/alerta autoritativo.

```powershell
$env:PYTHONPATH = "."
python -m pytest -q tests/test_parser_parity.py tests/test_judit.py tests/test_judit_normalization.py tests/test_rag_temporal_facts.py tests/test_retrieval.py tests/test_summary_output.py tests/test_anthropic_contract.py
$env:RAGV_M01_ARTIFACT_DIR = "$env:TEMP/ragvia-m01-new/document"
python scripts/validate_candidate_postgres.py --output "$env:TEMP/ragvia-m01-new/postgres" --test-path tests/integration/test_parser_parity_postgres.py
```

Execução local proporcional: **163 testes unitários direcionados aprovados**
na imagem Linux fixa (SDK/runtime pinados) e **53 integrações PostgreSQL
selecionadas aprovadas, zero skips**. Ruff e project harness aprovados. Isso
qualifica os contratos executados, não qualidade jurídica de um modelo real.
A CI obrigatória existente será associada ao SHA final, sem repetir a suíte
completa localmente.

O teste PostgreSQL exercita página Judit → webhook → worker/finalização →
gerador estruturado existente com SDK simulado → validação factual → persistência
→ API `iaSummary`. O ambiente é PostgreSQL/pgvector descartável por digest,
tmpfs e porta loopback, criado/removido pelo executor existente.
Comprova 13 fontes/IDs/índices, ordem inicial/final, horário
15:00, relações, isolamento de tenant, máscara e payload exato. Replay da fonte
finalizada não altera UUIDs/metadata de movimentos. Exporta Markdown/resposta
sintéticos apenas fora do Git. Pagamentos reais: zero; não é evidência Claude live.

A seleção ampliada abrange também finalização/atomicidade/idempotência/retention,
versões semânticas, API, proveniência, retrieval, sigilo e publicação inválida.
O primeiro conjunto ampliado teve 51 PASS/1 FAIL: a ordem textual parcial deslocou um
número explícito; a implementação foi corrigida sem alterar o teste protegido.
O primeiro conjunto unitário teve um FAIL pela adição do campo derivado no
header; a asserção passou a verificar esse campo separadamente, mantendo igualdade
exata de todos os campos originais. Logs completos e JUnit ficam em
`%TEMP%/ragvia-m01/`; o SHA final, seleção, resultados e hashes são registrados
em `qualification.json` e no PR draft, não herdados de uma revisão anterior.

## Arquivos alterados e revisão

- `app/judit.py`: envelopes, entidades, privacidade, fonte/ordem e integração dos labels.
- `app/judit_parser_rules.py`: menor fatia de regras/copied/adapted, com atribuição.
- `app/legal_facts.py`: formatação BRL de apresentação.
- `app/rag.py`: quarentena das anotações, número da fonte no contexto e horário sem “às”.
- `tests/test_parser_parity.py`, duas fixtures sintéticas, `tests/integration/test_parser_parity_postgres.py`.
- `tests/test_judit_normalization.py`: contrato aditivo do header, sem perder asserções anteriores.
- `NOTICE`, este relatório/evidência e registro/matriz dos contratos.

Nenhuma alteração em schema SQL, dependências, frontend, providers, budget,
autenticação, launcher ou instalação habitual. Nenhum dado original foi usado
em chamada externa; nenhuma chamada paga Claude/Judit/DataJud foi feita.

## Implantação, rollback e pendências explícitas

Somente novas normalizações recebem anotações/ordem novas. Não há backfill ou
reescrita automática de versões/sumários finalizados. JSONB aditivo é ignorado
por leitores antigos nas projeções existentes. Para aceitar envelopes novos de
página, atualizar/drenar todos os workers antes de ativar esse formato: workers
antigos não sabem desempacotá-lo. No rollback, pausar novos imports e drenar
jobs com envelopes novos antes de voltar o aplicativo; não alterar raw source
ou apagar/reordenar histórico para acomodar o binário antigo.

Permanecem intencionais: máscara mais restritiva; naive UTC; prioridade de números
explícitos; separação de advogados/partes; heurísticas em quarentena; nomes com
dígitos sujeitos ao validador conservador existente. Ordenação por número não
prova cronologia temporal quando a fonte é contraditória. Não foi implementada
resolução de identidade de pessoas homônimas nem correção automática do cadastro.
Licença do doador não é declarada no snapshot: a proveniência está no NOTICE;
revisão de licença/redistribuição cabe ao proprietário antes de release.

As PRs #11/#12 e a candidata 01D não são modificadas; não há merge, release ou
promoção à main. A migração fica em PR draft para revisão independente.
