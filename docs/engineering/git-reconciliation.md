# Reconciliação do histórico Git — baseline DataJud (snapshot histórico)

Data da inspeção: 2026-10-18 (reconciliação atualizada do Bloco 222). Este documento preserva o retrato daquela data; não representa o estado atual das branches/PRs. Para uma decisão de integração, faça nova reconciliação contra a main remota atual sem reescrever este snapshot.

## Referências na data da inspeção

Após `git fetch origin --prune` naquele momento:

```text
origin/main: f3869ba38959da72b679bf8e56cb30fcb1fda4ee
baseline tag: datajud-functional-baseline-216 -> f3869ba38959da72b679bf8e56cb30fcb1fda4ee
PR #505: merged, merge f3869ba
PR #506: OPEN, head 794ed51f624b187748bd3abaf224c1f6729b919b
```

A tag é anotada: o objeto da tag é `846649f0df7a618850d14f158745038164871818` e seu objeto apontado é exatamente `f3869ba`.

A análise foi executada na branch isolada `codex/block223-git-sanitization`, baseada na `origin/main`, sem alteração funcional.

Inventário quantitativo nesta execução:

```text
branches locais: 40
branches remotas (refs/origin): 444, excluindo `origin/HEAD`
nomes distintos após deduplicar local/remoto: 457
tags: 3
worktrees: 14, incluindo esta worktree de reconciliação
PRs retornadas pela consulta administrativa: 200
commits unreachable identificados pelo fsck: 25 (não classificados individualmente sem contexto de recuperação)
```

## Método

Foram usados:

- `git log --graph --decorate --all`;
- `git branch -vv`;
- `git worktree list --porcelain`;
- `git merge-base` e contagem left/right;
- `git cherry origin/main <branch>`;
- `git patch-id --stable` para os conjuntos de alterações;
- `git diff --stat` e `git range`;
- `git fsck --full --no-reflogs --unreachable`.

Mensagem semelhante não foi considerada prova de equivalência.

## Matriz de reconciliação

| Referência | SHA/head | Funcionalidade | Situação na main | Evidência | Ação recomendada |
|---|---|---|---|---|---|
| PR #505 / `codex/block196b-windows-homologation` | `4d3cab7` / merge `f3869ba` | Homologação Windows isolada, autenticação automática, DataJud configurador, contratos | **INTEGRATED** | PR #505 merged; branch contém zero commits exclusivos contra `origin/main` | Preservar baseline e tag |
| PR #504 / `codex/block194-desktop-update` | merge `a414b2a`; branch atual `3d377dc` | Atualização coordenada e testes de smoke | **INTEGRATED + UNIQUE_PENDING** | `a414b2a` está na main; `e1168f1`, `fae69c4`, `3d377dc` ficaram depois do commit integrado | Não excluir; revisar separadamente se o atualizador voltar ao escopo |
| PR #501 / `feat/block176-datajud-queue` | `1b16e06`, merge `36938c8` | Fila PostgreSQL para DataJud | **INTEGRATED** | PR #501 merged; comportamento atual está em `app/datajud_jobs.py`, queue e testes | Preservar código da main; branch histórica |
| PR #502 / `feat/datajud-independent-lookup` | `e95c742`, merge `097f271` | Lookup independente DataJud e classificação pública | **INTEGRATED** | PR #502 merged; implementação e testes estão na main | Preservar código da main; branch histórica |
| `feat/datajud-independent-metadata` | `44c5b03` | Limpeza de artefatos locais do staging | **UNIQUE_PENDING** | Branch local não contém a main; patch é limpeza de staging, não baseline funcional | Preservar até decisão explícita |
| `feat/block168-judit-operational-readiness` | `b7a1843` | Preparação Judit, provider selector e contratos | **SUPERSEDED/EQUIVALENT por etapas** | PR #500 integrou parte; depois PRs #501/#502 e #505 alteraram as mesmas fronteiras | Não cherry-pickar em massa |
| `feat/ragv-desktop` | `45871cc` | Launcher PyWebView e mocks Windows | **SUPERSEDED** | PR #496 e PR #499 integraram a evolução posterior; main contém `scripts/ragv_desktop.py` e testes atuais | Preservar como histórico; não reintegrar branch inteira |
| `fix/desktop-serves-current-frontend` | `fc570c3` | Revalidação de assets frontend no desktop | **INTEGRATED/SUPERSEDED** | PR #499 merged; branch atual diverge e contém apenas validações antigas | Não reintegrar branch inteira |
| `codex/block213-datajud-activation` | `8f885f1` | Manifesto e aceitação da instalação 198 | **EQUIVALENT** | Commits `0713d5f` + `8f885f1` preservados; patch-id do range `f3869ba..8f885f1` = `6ba895b...`, igual ao range `f3869ba..794ed51` | Usar PR #506 como versão canônica |
| `codex/block217-hygiene` / PR #506 | `794ed51` | Documentação e guardrails da baseline | **UNIQUE_PENDING** | PR #506 aberta; diff restrito a cinco arquivos; localmente 88 testes e harness passaram | Revisão manual; não integrar sem decisão |
| `codex/block221-installer-contract` | `fdc5008` | Contrato Inno Setup e atalhos opcionais | **UNIQUE_PENDING** | `merge-base=f3869ba`; `origin/main..fdc5008` contém exatamente 1 commit e 2 arquivos (`RagVDesktop.iss`, `test_windows_homologation.py`) | Preservar; avaliar em PR própria; requer validação com ISCC antes de integrar |
| `codex/block223-git-sanitization` | branch atual | Este relatório | **UNIQUE_PENDING** | Base exata `origin/main`; somente documentação neste bloco | Não publicar sem autorização |
| `recovery/block209-4d3cab7` | `4d3cab7` | Recuperação da branch #505 | **HISTORICAL** | Referência local aponta para commit já ancestral da main | Manter até política de retenção aprovada |

## Equivalência da baseline documental

Os commits `0713d5f` e `8f885f1` formam uma cadeia sobre `f3869ba`. A consolidação `794ed51` parte diretamente de `f3869ba`.

Comparação objetiva:

```text
patch-id do range f3869ba..8f885f1  = 6ba895b4b0164ce8f2aa187ed0272ca224b55142
patch-id do range f3869ba..794ed51   = 6ba895b4b0164ce8f2aa187ed0272ca224b55142
```

Portanto, a PR #506 contém a versão canônica suficiente. Não é necessário integrar `0713d5f` e `8f885f1` separadamente.

O patch-id do commit isolado `794ed51` não é usado para essa conclusão, pois ele é uma consolidação de dois commits anteriores; a comparação correta é do range completo.

## PRs Judit e Desktop ainda relevantes

A consulta administrativa identificou a PR aberta `#487` (`feat/local-desktop-launcher`, head `b4ee1c8`). Ela contém três commits exclusivos e 135 linhas em documentação, launcher local e testes. O merge-base da PR é `d64cad1`; ela não é ancestral da main nem equivalente à baseline Windows 198. A PR está `OPEN`, com `mergeState=UNKNOWN`, check CI falho, Codacy `ACTION_REQUIRED` e dois status Vercel falhos. Deve ser tratada como `UNIQUE_PENDING`, não arquivada por idade.

O conteúdo de #487 acrescenta `scripts/desktop_launcher.py`, `docs/desktop-launcher.md` e `tests/test_desktop_launcher.py`. Não foi demonstrado que essa camada opcional seja necessária para o launcher de homologação 198; não deve ser integrada em massa.

As PRs #499, #500 e #501 foram integradas e seus efeitos estão representados na main por seus respectivos merges e evoluções posteriores. As PRs Judit antigas #369, #370–#396, #407, #430, #431, #435 e #466 aparecem como integradas no histórico remoto. Nenhuma PR aberta de Judit foi classificada como pronta para integração nesta reconciliação.

## Main e implementação efetiva

A `origin/main` contém:

- desktop PyWebView e injeção de Bearer gerenciado;
- autenticação e isolamento de tenant;
- fila DataJud PostgreSQL e jobs autenticados;
- lookup independente DataJud com classificação pública;
- configurador Windows e Compose de homologação;
- contratos de regressão da PR #505.

A baseline funcional da instalação 198 não é inferida apenas da main: está preservada pela tag, pelo manifesto sanitizado e pelas evidências dos blocos 214–216.

## Reversões e trabalhos desfeitos

Não foi encontrado commit de reversão explícito para os componentes DataJud/Desktop priorizados. A busca por `Revert` em seus caminhos não demonstrou substituição por revert.

O histórico mostra reaplicações/linhas paralelas de DataJud (`b7a1843`/`be75380`, `0fc5067`/`db89031`, `e95c742`/`097f271`), mas a equivalência foi atribuída somente quando a PR/merge e o conteúdo atual permitiram demonstrá-la. Não há prova de que os commits locais antigos devam ser apagados.

Há branches antigas com conteúdo divergente, mas divergência não foi tratada como abandono. Não foram removidas branches, worktrees ou referências.

## Worktrees e recuperação

Worktrees existentes foram somente inventariadas. Incluem blocos 192, 194, 196, 213, 217, 221 e linhas antigas de DataJud/identidade visual.

`git fsck` encontrou objetos unreachable, incluindo commits `038731a`, `3d0d850` e `441197b`, mas sem contexto suficiente para classificá-los ou removê-los. Permanecem recuperáveis pelo objeto Git enquanto não houver prune.

## Documentação canônica

A fonte atual da aceitação DataJud é:

```text
docs/engineering/evidence/datajud-functional-baseline-216.md
```

Os registros de contrato continuam em:

```text
docs/engineering/feature-registry.md
docs/engineering/validation-matrix.md
docs/engineering/regression-policy.md
```

A PR #506 é a fonte canônica da documentação de baseline; os commits 0713d5f/8f885f1 permanecem como histórico local, não como dependência adicional de integração.

## Inventário bruto de worktrees

No momento da inspeção, as worktrees ativas eram:

```text
D:/projetos/RagV                         feat/block179-datajud-secrecy
D:/temp/ragv-block192-main               detached 296db00
D:/temp/ragv-block194                  codex/block194-desktop-update
D:/temp/ragv-block196a                 codex/block196b-windows-homologation
D:/temp/ragv-block213                 codex/block213-datajud-activation
D:/temp/ragv-block217                 codex/block217-hygiene
D:/temp/ragv-block221                 codex/block221-installer-contract
D:/temp/ragv-block221-recon            codex/block221-git-reconciliation
D:/temp/ragv-block222                 codex/block222-git-reconciliation
D:/temp/ragv-block223                 codex/block223-git-sanitization
D:/temp/ragv-datajud-block190          feat/datajud-independent-metadata
D:/temp/ragv-datajud-clean190          feat/datajud-independent-lookup
D:/temp/ragv-visual-block190a          style/grupo-villela-branding
D:/temp/ragv-visual-clean190a          style/grupo-villela-identity
```

As branches remotas e locais completas foram capturadas por `git branch -vv`; não houve exclusão nem alteração de referência durante o inventário.

## Classificação operacional A–G

**A — Integrados:** PRs #496, #499, #500, #501, #502, #504 e #505, confirmadas por merges remotos e ancestralidade/conteúdo.

**B — Exclusivos preservados:** `0713d5f`, `8f885f1`, `fdc5008`, `e1168f1`, `fae69c4`, `3d377dc`, `b4ee1c8` e os commits das linhas antigas de DataJud/Judit.

**C — Equivalentes comprovados:** o range `f3869ba..8f885f1` e o range `f3869ba..794ed51` têm o mesmo patch-id estável `6ba895b4b0164ce8f2aa187ed0272ca224b55142`.

**D — Revertidos:** nenhum caso comprovado nos caminhos priorizados.

**E — Arquiváveis futuramente:** somente branches/worktrees sem commits exclusivos, após retenção da tag, recovery refs e evidências. Nenhuma foi arquivada neste bloco.

**F — PRs independentes:** PR #506 (documentação/guardrails), `fdc5008` (Inno/atalhos), PR #487 (launcher local) e os três commits do atualizador (`e1168f1`, `fae69c4`, `3d377dc`). Cada grupo tem escopo e validação distintos.

**G — Não removíveis agora:** tag `datajud-functional-baseline-216`, `recovery/block209-4d3cab7`, instalação 198, perfil/volumes, worktrees históricas, evidências e objetos unreachable sem análise de recuperação.

## Contagens por decisão

As contagens abaixo são de grupos relevantes efetivamente comparados, não uma classificação semântica automática de todas as 456 referências distintas:

```text
INTEGRATED:          7 PRs relevantes (#496, #499, #500, #501, #502, #504, #505)
EQUIVALENT:          1 grupo documental (0713d5f + 8f885f1 ~= 794ed51)
SUPERSEDED_VERIFIED: 2 linhas (desktop antigo e preparação Judit por etapas)
REVERTED:           0 comprovados
UNIQUE_PENDING:      5 grupos (#506, fdc5008, PR #487, três commits do atualizador, metadata cleanup)
KEEP_RECOVERY:       2 referências explícitas (tag baseline e recovery/block209)
HISTORICAL:          worktrees/branches dos blocos anteriores e evidências
UNKNOWN:             25 commits unreachable + refs não analisadas semanticamente
```

## Plano de saneamento

### INTEGRAR

Nenhum grupo deve ser integrado automaticamente. PR #506 está tecnicamente pronta para revisão manual, mas mantém exceção de CI; `fdc5008` requer compilação ISCC e revisão do contrato de instalação; PR #487 requer decisão de produto sobre launcher opcional e correção de seus checks; o atualizador requer validação A→B, assinatura e rollback.

### ARQUIVAR FUTURAMENTE

Somente após confirmação de que não há commits exclusivos necessários, worktree limpa, referência de recuperação e decisão do proprietário. Candidatos iniciais: branches já integradas cuja ponta local não contenha commits exclusivos adicionais. Nenhum candidato foi arquivado neste bloco.

### INVESTIGAR

- `e1168f1`, `fae69c4`, `3d377dc`: atualizador Windows pós-PR #504, ainda exclusivo.
- `b4ee1c8`, `47e4f5a`, `6c56be1`: launcher desktop opcional da PR #487.
- objetos unreachable do `fsck`.
- branches remotas sem PR ativa e sem relação funcional demonstrada.

## Candidatos a higiene futura

Nenhuma limpeza foi executada. Candidatos que exigem decisão explícita:

- branches locais pós-merge com trabalho exclusivo de atualização (`e1168f1`, `fae69c4`, `3d377dc`);
- `codex/block221-installer-contract`;
- worktrees históricas;
- objetos unreachable do `fsck`;
- artefatos de staging não rastreados.

Não são candidatos seguros enquanto não houver confirmação de ausência de uso, backup e decisão do proprietário.

## Ordem recomendada

1. Revisar/decidir PR #506.
2. Decidir separadamente o contrato Inno/atalhos de `fdc5008`.
3. Só depois avaliar branches pós-merge de atualização.
4. Manter a tag `datajud-functional-baseline-216` e a recuperação `recovery/block209-4d3cab7`.
5. Não remover instalação 198, perfil, volumes, evidências ou worktrees durante essa higiene.

Nenhuma consulta externa, alteração funcional, exclusão ou reescrita de histórico foi realizada neste bloco.
