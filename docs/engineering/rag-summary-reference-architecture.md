# RAG de resumo Judit — arquitetura de referência recuperada

## Proveniência

Esta nota recupera e preserva, sem copiar dados de caso, a arquitetura documentada no `rag_resumo/README.md` do ZIP de referência fornecido. Identidade do arquivo de origem: `rag_resumo.zip`, SHA-256 `08e9abbac3f46330c95a4529b17a034303b9dba12e17839d676a79921e5fb445`; o código correlato do checkout doador foi identificado no commit `d6d7a43f9c4cdbd0f25c9a76ea9276c68533932b`. O README descreve formato `summary_ia` e um pipeline local separado. O fixture e o template de saída contêm conteúdo de processo; eles não foram reproduzidos aqui.

Este registro é uma síntese rastreável do documento original, não uma substituição de `docs/engineering/rag-summary-parity.md`. A avaliação das adaptações para o produto está em `docs/engineering/rag-summary-parity.md`.

## Arquitetura descrita na referência

```text
JSON Judit (response_type=lawsuit)
  → parser: fatos normalizados, máscara de documentos, datas e alertas
  → knowledge: glossário e recuperação de textos de apoio
  → prompt: redação limitada aos fatos/contexto recuperados
  → renderer: estrutura fixa e sanitização
  → summary_ia (Markdown/JSX)
```

O documento atribui deterministicamente ao código a normalização de fatos, mascaramento de CPF/CNPJ, distinção entre data de movimento e data agendada, leitura de períodos/destinatários, alertas cadastrais e layout final. Ao modelo atribui apenas a redação de introdução, descrições de partes, explicações de classe/assunto e narrativa de movimentações apoiadas nas fontes recuperadas.

A base de conhecimento original contém arquivos locais JSONL/NumPy, tenta primeiro código TPU, depois termo/sinônimo e por último similaridade vetorial. O README sugere regenerar o índice ao editar definições e lista como limitações o uso heurístico dos alertas e a ausência de leitura de anexos. O gerador descrito também prevê fallback por template quando a API falha e um modo que dispensa o modelo; processos sigilosos ficam fora do caminho externo salvo autorização explícita.

## Decisões de adaptação no RagV

- A normalização, geração, recuperação, validação, persistência e publicação permanecem no pipeline canônico do RagV; não foi criado outro engine nem uma camada paralela de integração.
- O TPU oficial versionado do RagV substitui o índice local genérico do doador. Definições não encontradas não são inventadas.
- São preservados apenas os fatos de data/período extraídos por padrões restritos; datas de vencimento não são calculadas. Alertas conjecturais não são promovidos a afirmações jurídicas.
- O renderer determinístico do RagV preserva seu esquema e adiciona projeção allowlisted de representantes e metadados. A geração inválida continua sem publicação; não se adota o fallback de template do doador.
- Sigilo segue a política local do RagV e não chama provedor externo. Dados de representantes e os novos metadados de cadastro não são enviados ao provedor.

A lista de requisitos, diferenças ainda abertas e evidências locais está na matriz de paridade vinculada acima. Esta nota não afirma equivalência completa com a implementação do ZIP, qualidade de modelo ao vivo, nem validação com dados de produção.
