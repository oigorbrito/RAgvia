# Política de proteção contra regressões

## Objetivo

Preservar contratos já comprovados sem transformar o harness em uma segunda suíte de testes ou em uma aprovação permanente de produção.

## Antes de editar

1. Identificar os IDs afetados em `feature-registry.md`.
2. Classificar a afirmação como invariável, hipótese ou evidência.
3. Localizar a menor camada que pode impedir a regressão.
4. Verificar configuração persistente, tenant, banco, volume e fronteiras externas envolvidos.
5. Registrar o objetivo/falha no PR ou relatório da mudança.

## Durante a mudança

- Não substituir mecanismo funcional sem justificativa e evidência comparativa.
- Não apagar ou sobrescrever mudanças não commitadas.
- Não aplicar defaults por cima de configuração persistente durante bootstrap, instalação, atualização ou reabertura.
- Não reintroduzir login por senha ou Bearer manual na homologação sem decisão explícita.
- Não confundir autorização ausente com defeito técnico.
- Não modificar a instalação habitual para validar a homologação.
- Não executar Judit pago ou DataJud externo sem autorização específica.
- Não registrar tokens, chaves, payloads ou dados processuais.

## Validação mínima

A matriz em `validation-matrix.md` define o teste direcionado obrigatório. Quando o contrato atravessar processos, Compose, PostgreSQL, WebView2 ou provedor externo, teste unitário isolado não é suficiente. A validação deve declarar:

- commit e artefato efetivamente testados;
- comando, fixture e ambiente;
- resultado legível/máquina quando disponível;
- escopo e lacunas;
- se é evidência sintética, de integração ou real.

Uma falha direcionada bloqueia a declaração de PASS. Uma lacuna de evidência permanece lacuna; não é preenchida por inferência de uma execução antiga.

## Configuração persistente

Para qualquer configuração durável, o teste deve demonstrar:

1. valor inicial;
2. escrita autorizada;
3. valor lido pelo processo efetivo;
4. reinício/reabertura;
5. valor preservado;
6. ausência de alteração em outro perfil/tenant.

DataJud tem uma proteção adicional: flags `DATAJUD_ENABLED` e `DATAJUD_AUTHORIZED_USE` devem permanecer coerentes entre API e workers; a chave deve permanecer fora da API; `/capabilities` é a confirmação do estado do backend. A ausência de autorização deve produzir `ATIVACAO_PENDENTE_DE_AUTORIZACAO`, não ativação artificial.

A baseline funcional aceita em 2026-10-18 é a instalação `RagV Homologacao 198`, no commit de código `f3869ba38959da72b679bf8e56cb30fcb1fda4ee`, com evidência em `docs/engineering/evidence/datajud-functional-baseline-216.md`. Alterações em `app/datajud*`, `app/frontend/*`, `scripts/ragv_desktop.py`, launcher/Compose ou injeção de autenticação devem executar os contratos `DATAJUD-STATE-001`, `DATAJUD-RESTART-001`, `DESKTOP-BACKEND-001`, `DESKTOP-AUTH-001` e `DATAJUD-SELECTOR-001`. `DATAJUD-E2E-001` só pode ser aceito com consulta externa expressamente autorizada; a baseline não o aprova. A captura visual deve ser sanitizada e tokens/chaves nunca podem entrar em logs, Git ou artefatos.

## Controle leve de impacto

Mudanças em `app/auth.py`, `app/api.py`, `app/datajud*`, `app/frontend/*`, `scripts/ragv_desktop.py`, `scripts/bootstrap_homologation.py`, Compose, SQL, launcher/installer Windows ou workers devem apontar os IDs afetados e executar a seleção correspondente da matriz. O controle pode ser um teste/checagem estática simples; não introduzir sistema paralelo de governança.

## Mutação reversível

Quando viável, provar um guardrail com uma mutação temporária em cópia/fixture isolada: remover a proteção, executar o teste, observar falha e restaurar o arquivo. A mutação nunca deve atingir o banco, volume ou instalação habitual e não constitui evidência funcional do produto.

## Encerramento

O relatório deve separar `PASS`, `FAIL`, `BLOCKED` e lacunas. `RAGV_REGRESSION_GUARD_ESTABLISHED` significa que documentação, testes direcionados e limitações estão versionados; não significa que todos os provedores externos estejam ativos.
