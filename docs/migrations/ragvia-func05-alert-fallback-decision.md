# RAGVIA-FUNC05 — Paridade de alertas e decisão sobre fallback do RagResumo

## Resultado e escopo

Comparação do doador privado `oigorbrito/rag_resumo` em `0d0f8cbb495292a3f3d61fba75428f3136059ea8` com o pipeline único RAgvia (base M04-F2, PR #18). O objetivo é concluir **funcionalidade observável**, não incorporar uma segunda geração ou contornar publicação validada.

### Inventário de comportamento

| Recurso do doador | Local | Destino no RAgvia | Decisão |
| --- | --- | --- | --- |
| Alertas léxicos de classe, área e vara/órgão incompatíveis | `resumo/parser.py` | `app/judit_parser_rules.py`; `header.source_parser.indicators` | **ADAPT / preservado**: observações heurísticas com referências, nunca correções automáticas dos dados registrados |
| Classes secundárias suspeitas | `resumo/parser.py` | `header.source_parser.classifications` | **ADAPT / preservado**: não substituir a classe canônica por inferência |
| Resumo visual, partes, representantes, classe, assuntos, cronologia | `resumo/render.py` | `app/summary_output.py`, frontend `iaSummary` | **Feito em DOC01**; sem JSX arbitrário |
| Template completo sem provedor | `GeradorTemplate.gerar` | Não integrado à geração pública | **DROP**: o template faz inferências sobre pedidos/responsabilidade/citação sem comprovação individual nem claims estruturadas verificadas |
| Preenchimento parcial de campos de geração por template | `completar()` | Não integrado | **DROP**: preencher lacunas de uma resposta do modelo cria texto material que não passou pela proveniência da própria resposta |
| Fallback automático após exceção do gerador | `gerar_resumos(..., fallback_template=True)` | `app/rag.py:generate_summary` | **DROP**: falha de credencial/geração propaga erro, sem publicação pública alternativa |
| Resumo mínimo de processo sigiloso | Não equivalente ao template público | `_secret_summary` e validação restrita | **KEEP**: comportamento canônico independente, não transporte do fallback doador |

## Defeito FUNC05 corrigido

A estrutura `header.source_parser` já era excluída do contexto do provedor, mas objetos de movimentação `steps[*].metadata.source_parser` ainda passavam por `_provider_payload`. Eram classificações léxicas não comprovadas, e não dados que o Claude deve tratar como prova. Agora somente essa anotação é omitida nas movimentações destinadas ao modelo, preservando texto dos atos, outras propriedades e ordem. A cópia não altera `context` nem a persistência de origem.

A regressão sintética em `tests/test_func05_alert_fallback_guardrails.py` confirma a quarentena dos alertas do cadastro e das movimentações e a preservação da classificação processual canônica. Dois casos adicionais bloqueiam ativação silenciosa do fallback público quando falta `ANTHROPIC_API_KEY` ou quando o provedor retorna falha, sem chamadas de rede.

## Segurança de publicação e compatibilidade

Nenhuma alteração de schema-v2/v3, SQL, KB, prompt, assinatura do renderer ou política de correção por uma única tentativa. `iaSummary` permanece o artefato de saída validado. Quando a geração pública não satisfaz os contratos, não há autorização para publicar a prosa de template como se fosse um resumo jurídico validado. A saída restrita de sigilo mantém o contrato próprio e não é o fallback para processos públicos.

## Evidência e limites

A CI da correção de produção `8aa35390ab9c56955c9056fbdd104edd93507a46`, run `40152468536`, passou. O commit com os testes adicionais de fallback e este documento precisa de CI própria; não herda aprovação da execução anterior. Não houve dados judiciais reais, chamada paga, merge nem release. Esta decisão fecha o escopo de transporte funcional de alertas/fallback, com diferenças deliberadas registradas; não estabelece qualidade semântica de Claude real.
