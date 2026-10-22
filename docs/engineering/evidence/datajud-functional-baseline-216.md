# Baseline funcional DataJud — Bloco 216

Data de aceitação: 2026-10-18

## Referência de código

- Commit de merge: `f3869ba38959da72b679bf8e56cb30fcb1fda4ee`
- Referência anotada: `datajud-functional-baseline-216`
- PR de origem: #505

A aceitação manual foi feita na instalação `RagV Homologacao 198`. Os arquivos de código efetivamente embarcados no runtime foram comparados por SHA-256 com o commit acima; os arquivos centrais abaixo coincidem. O overlay de Compose possui substituição esperada da tag da imagem pelo empacotador.

## Ambiente e artefatos

- Instalador/runtime: `C:\Users\igorb\AppData\Local\Programs\RagV Homologacao 198`
- Executável: `RagVDesktop.exe`
- SHA-256 do executável: `B97BE3ACD375C9E3EBF073EBB34D41206B841264E14D1D58BC99CFB9C7C17059`
- Pacote: `0.1.5-hml`
- Package SHA declarado: `757c42f4b237e371b839c45dbe404a009a0dcea1`
- Imagem: `ragv-homologacao:757c42f4b237`
- Digest da imagem: `sha256:0c1bbff8b5c14345ce6e08fecdde4462ac4e0d7253290485d7a663325058c362`
- Projeto Compose: `ragv-hml-76460759f1c0`
- API: `127.0.0.1:62427`

O manifesto instalado declara `Application base SHA: a414b2a...`; esse campo é metadado histórico do empacotamento e não substitui a comparação dos arquivos do runtime com o commit de merge. O inventário não contém credenciais.

Hashes SHA-256 dos arquivos centrais instalados (coincidentes com o commit de merge, exceto a tag de imagem substituída no overlay):

```text
app/api.py                 f0806adbc058ac7e97947b3936c4e64f7f64d644e488526c575e2a13c662cb2f
app/auth.py                5971cdd326abc63eb78dcc660e1ae6dbea606b4f2300edbc3c45771c29c5b7cd
app/datajud_client.py      f6579d19ce926a5912ca9a864c381ad20f6df21ff3ac82ceefde4af01806bb97
app/datajud_jobs.py        1d91a6a11043e43b472250dda03c30ff1cbe91db4b3e3ea0214ef99090f144cd
app/frontend/app.js        2b2906058a2f2eb667bf40dc231102dac26fbbbe5a4f754058f2a920621644fbb
app/frontend/index.html    05757be62ef5e57de9b8934d44233f3dbb9c63682e7edad1e3f275a0a6959a4d
compose.yaml               cfc0b45d240ecb70afa40d48250a8886e5d4b2b5c9ef02099040e85a201d3e44
scripts/ragv_desktop.py   593db99c96cd8ea9194ae4c8acade0ed6c5a7f823a39eb25538be47edf70bb8f
```

## Evidência manual

Blocos 214–215, com confirmação manual do usuário:

- `/ready`: HTTP 200.
- `/capabilities`: HTTP 200, `sources.datajud.enabled=true`.
- API e scheduler sem `DATAJUD_API_KEY`.
- `worker-1` e `worker-2` com a chave presente, sem exposição do valor.
- `/auth/verify`: HTTP 200 a partir da janela.
- Janela PyWebView aberta pelo executável 198.
- Bearer automático autenticado.
- Seletor DataJud visível e habilitado na captura sanitizada `D:\temp\ragv-block215-window-sanitized.png`.
- Serviços recriados e configuração preservada.
- Nenhuma consulta processual DataJud ou Judit foi executada.

## Contratos protegidos

- `DATAJUD-STATE-001`
- `DATAJUD-RESTART-001`
- `DESKTOP-BACKEND-001`
- `DESKTOP-AUTH-001`
- `DATAJUD-SELECTOR-001`
- `DATAJUD-E2E-001` — preparado para aceitação futura autorizada; permanece pendente.

Esta evidência aprova a ativação, ligação da janela, autenticação e seletor. Não aprova consulta processual real, qualidade de dados do provedor ou integração Judit.
