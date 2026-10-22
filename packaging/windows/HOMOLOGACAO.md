# RagV Homologação — Grupo Villela

Pacote de homologação, **não é release de produção**. Base da aplicação: `a414b2a1d478ccfbf33611a7c40437dd3f90f3b7`. A revisão do pacote fica no nome do instalador e no diagnóstico instalado.

## Pré-requisitos

Windows 10/11 x64, WebView2 Runtime e Docker Desktop iniciado no modo **containers Linux**, com Compose 2.24.4 ou superior. Internet é necessária na primeira abertura para baixar dependências e construir a imagem. Não é necessário instalar Python, Git ou executar comandos.

## Instalar e utilizar

1. Execute `RagVDesktop-Homologacao-0.1.3-hml-<SHA>-Setup.exe`. Pode escolher uma pasta com espaços. A instalação é por usuário, sem elevação de administrador.
2. O pacote completo fica na pasta escolhida: executável, scripts e subpasta `runtime`. A cópia é verificada pelo instalador antes de oferecer a abertura. Não selecione um checkout Git nem a pasta da instalação habitual.
3. Inicie Docker Desktop e abra o atalho **RagV Homologação**. Uma janela de progresso acompanha o preparo inicial; pode demorar no primeiro build. Nas próximas aberturas, a imagem desta revisão é reutilizada.
4. A homologação já inicia autenticada: o desktop injeta automaticamente o Bearer padrão de teste e não solicita senha nem token manual. O atalho **Configurar acesso** informa que o ambiente já está autenticado.
5. O Histórico começa vazio e persiste no banco próprio. Fechar a janela não remove banco, dados ou serviços. Reabrir usa o mesmo tenant.

O perfil `%LOCALAPPDATA%\RagV-Homologacao` guarda `.env`, o token padrão de teste e diagnósticos com permissões limitadas ao usuário e SYSTEM. O instalador **nunca** inclui credenciais reais. Um perfil de homologação anterior válido é preservado; nunca se importa configuração do projeto habitual. O nome Compose é `ragv-hml-<identificador aleatório>`, com volume PostgreSQL exclusivo. PostgreSQL não publica porta; a API usa somente loopback, escolhendo porta livre na primeira abertura. Se uma porta anteriormente escolhida passar a ser ocupada, não remova volumes: feche o serviço conflitante e reabra a homologação.

## Configurar DataJud sem terminal

1. Confirme que sua finalidade e seu uso são autorizados pelas regras atuais do CNJ.
2. Na tela **Configurar acesso**, quando o backend informar que o DataJud está indisponível, clique em **Configurar DataJud**; o mesmo fluxo também está disponível no atalho **Configurar DataJud - RagV Homologação**. O link aponta para a fonte oficial: <https://datajud-wiki.cnj.jus.br/api-publica/acesso/>. Use **Obter chave oficial** para ler a chave vigente diretamente da documentação oficial; essa etapa não faz consultas processuais.
3. Cole a chave no campo mascarado, marque a confirmação de uso autorizado e clique **Salvar e ativar**. A chave fica restrita aos workers; API, scheduler e desktop não a recebem. Não é necessária chave Judit.
4. Após salvar com a confirmação explícita, a API e os workers são recriados no projeto de homologação; a interface reconsulta `/capabilities` e só então libera o seletor DataJud. Nenhuma consulta paga ou processual é feita pelo configurador.

## Diagnóstico e recuperação

Não há recuperação de senha nesta edição: a homologação usa exclusivamente o token padrão local `ragv-hml-local-test-2026`. O Docker, tenant e Histórico são preservados; nenhum volume é removido.

- `runtime/HOMOLOGACAO.txt`, na pasta instalada: versão, SHA base e SHA do pacote.
- `%LOCALAPPDATA%\RagV-Homologacao\diagnostics.txt`: revisão, projeto, porta e runtime efetivos.
- `startup.jsonl`: etapas, códigos de saída e versão/edição do PowerShell; sem tokens, DSN, saída de containers ou dados processuais.
- `last-error.txt`: última falha de inicialização. O aplicativo não abre se não conseguir validar o tenant.

Os atalhos usam explicitamente Windows PowerShell 5.1. As ACLs são aplicadas e verificadas pelas APIs .NET, sem dependência de `Get-Acl` ou do módulo `Microsoft.PowerShell.Security`. Scripts têm UTF-8 com BOM para preservar acentos nesse PowerShell.

Reinstalar o pacote recompõe `runtime` sem excluir o perfil/banco. Desinstalar não remove volumes automaticamente. Não use comandos globais de limpeza Docker nem remova o projeto/volume habitual `ragv` / `ragv_rpy_pgdata`.

## Build reproduzível (mantenedores)

Na revisão identificada do pacote, use PowerShell 7 e execute `scripts/package_windows_homologation.ps1 -PackageSha <SHA completo>`. O build cria venv descartável, usa dependências fixadas e verifica Inno Setup engine 6.7.3. O SHA-256 gerado identifica o executável efetivamente produzido; builds em momentos diferentes não prometem identidade binária das imagens reconstruídas a partir de repositórios externos.
