# ADR-0001 — Canal de atualização Windows da homologação

- **Status:** aceito como decisão de preparação; aplicação bloqueada por pré-requisitos
- **Data:** 2026-10-18
- **Escopo:** desktop Windows de homologação

## Contexto

O desktop atual é um executável PyWebView empacotado com PyInstaller e instalado por Inno Setup. A aplicação coordena um runtime Docker Compose com API, migração, dois workers, scheduler, PostgreSQL, tenant e Histórico. Uma atualização parcial do EXE não atualiza o frontend/container; uma atualização parcial da imagem também pode deixar API, migração e workers em revisões incompatíveis.

A configuração persistente (`%LOCALAPPDATA%\RagV-Homologacao\.env`) e o volume PostgreSQL não podem ser substituídos por defaults ou removidos durante atualização. Migrações exigem compatibilidade e rollback explícito.

## Alternativas avaliadas

| Alternativa | Compatibilidade | Segurança/rollback | Custo | Decisão |
|---|---|---|---|---|
| WinSparkle | Acrescenta runtime nativo e appcast; não integra naturalmente a atualização coordenada do EXE + Compose + migrações. | Exige artefatos e appcast assinados, certificado e canal confiável, inexistentes no ambiente atual. | Alto | Não escolher agora. |
| MSIX/App Installer | Exige identidade MSIX, pacote assinado, certificado, manifesto e mudança do modelo de instalação `{localappdata}`/Inno. Docker/runtime e preservação do perfil exigiriam migração própria. | Bom quando o pipeline de assinatura/distribuição existe; não existe neste ambiente. | Alto | Não escolher agora. |
| Atualizador externo mínimo usando Inno | Reutiliza o instalador existente e permite executar fora do processo substituído. Pode coordenar backup, instalação, migração, health check e rollback. | Só é aceitável com manifesto assinado, EXE assinado, hash, publisher/certificado pinado, backup verificado e pacote A→B descartável. | Menor | Escolhido como alvo, ainda bloqueado. |

Referências oficiais consultadas para a avaliação: [WinSparkle](https://winsparkle.org/), [MSIX/App Installer](https://learn.microsoft.com/windows/msix/app-installer/).

## Decisão

Preparar um atualizador externo mínimo baseado no Inno Setup, mas **não habilitá-lo nem adicionar um botão operacional antes dos pré-requisitos**:

1. certificado de assinatura de código e publisher confiável;
2. manifesto versionado com assinatura verificável e canal autenticado;
3. artefato A→B imutável;
4. backup/restore verificado;
5. teste descartável de migração, preservação de `.env`, tenant, Histórico e rollback;
6. health check de API, workers, scheduler e migração;
7. plano de recuperação que não execute `down -v` nem toque no volume habitual.

O instalador atual foi comprovado como `NotSigned`; portanto, rejeitar sua aplicação é o comportamento seguro. Não substituir assinatura por SHA-256: hash prova integridade do arquivo recebido, não autenticidade da origem.

## Consequência

Este bloco entrega o contrato e o gate verificável, não uma atualização automática declarada como funcional. O bloqueio é de cadeia de confiança/artefato e rollback, não de PyWebView ou do cliente DataJud. O futuro botão deverá chamar um processo externo e permanecer desabilitado quando não houver manifesto e instalador válidos.

## Verificação do Bloco 206

A máquina possui `New-SelfSignedCertificate`, `Set-AuthenticodeSignature` e `Get-AuthenticodeSignature`, mas não possui `signtool.exe`, `osslsigncode` ou uma identidade de assinatura confiável do projeto. A possibilidade de criar um certificado autoassinado não satisfaz a cadeia de confiança de produção; por isso não foi usada para declarar atualização segura.

O verificador foi exercitado contra uma cópia do instalador atual e rejeitou o pacote com `NotSigned`. Também foram cobertos por testes/checagens os gates de manifesto ausente, versão inferior, hash divergente e assinatura inválida. Não houve aplicação A→B, pois o pré-requisito de assinatura e rollback não está satisfeito.

O resultado deste bloco é `UPDATE_BLOCKED_BY_TRUST_CHAIN`, não `UPDATE_PASS`. Nenhum botão operacional foi adicionado ao PyWebView e nenhuma instalação foi alterada.
