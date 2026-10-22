$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'homologacao-common.ps1')
Add-Type -AssemblyName System.Windows.Forms
$progress = $null
$script:HmlDefaultToken = 'ragv-hml-local-test-2026'
try {
    Initialize-HmlPaths
    $progress = New-Object Windows.Forms.Form
    $progress.Text = 'RagV Homologação — inicialização'
    $progress.Size = New-Object Drawing.Size(540,150)
    $progress.StartPosition = 'CenterScreen'
    $progress.FormBorderStyle = 'FixedDialog'
    $progress.ControlBox = $false
    $script:HmlStatusLabel = New-Object Windows.Forms.Label
    $script:HmlStatusLabel.Dock = 'Fill'
    $script:HmlStatusLabel.Padding = New-Object Windows.Forms.Padding(20)
    $script:HmlStatusLabel.Text = 'Preparando o ambiente de homologação…'
    $progress.Controls.Add($script:HmlStatusLabel)
    $progress.Show()
    [Windows.Forms.Application]::DoEvents()
    if (-not (Test-Path $script:HmlEnv)) {
        function New-HmlSecret {
            $bytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
            return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
        }
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 18080)
        try { $listener.Start() } catch { $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0); $listener.Start() }
        try { $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port } finally { $listener.Stop() }
        $token = $script:HmlDefaultToken
        $tenant = [guid]::NewGuid().ToString()
        $projectName = 'ragv-hml-' + [guid]::NewGuid().ToString('N').Substring(0,12)
        $newEnv = "$($script:HmlEnv).new"
        @(
            "COMPOSE_PROJECT_NAME=$projectName"
            "POSTGRES_PASSWORD=$(New-HmlSecret)"
            "RPY_OPS_TOKEN=$(New-HmlSecret)"
            "JUDIT_WEBHOOK_TOKEN=$(New-HmlSecret)"
            "RPY_API_PORT=$port"
            'RPY_TENANT_PROCESSES={}'
            "RPY_BEARER_TOKENS={`"$token`":`"$tenant`"}"
            'DATAJUD_ENABLED=false'
            'DATAJUD_AUTHORIZED_USE=false'
            'DATAJUD_API_KEY='
            'JUDIT_API_KEY='
            'ANTHROPIC_API_KEY='
            'OPENAI_API_KEY='
        ) | Set-Content -LiteralPath $newEnv -Encoding ASCII
        Set-HmlPrivatePath $newEnv
        $tokenFile = Join-Path $script:HmlRoot 'access-token.txt'
        Set-Content -LiteralPath $tokenFile -Value $token -Encoding ASCII
        Set-HmlPrivatePath $tokenFile
        Move-Item -LiteralPath $newEnv -Destination $script:HmlEnv
    }
    $settings = Get-HmlSettings
    $mapping = $settings.RPY_BEARER_TOKENS | ConvertFrom-Json
    $env:RPY_DESKTOP_TOKEN = $script:HmlDefaultToken
    $env:RPY_HOMOLOGATION_MODE = 'true'
    $env:RPY_HOMOLOGATION_BIND_ADDRESS = '127.0.0.1'
    $compose = Get-HmlComposeArgs $settings
    $metadata = Get-Content (Join-Path $script:HmlRuntime 'HOMOLOGACAO.txt') -Encoding UTF8
    $packageSha = (($metadata | Where-Object { $_ -like 'Package SHA: *' }) -replace '^Package SHA: ', '')
    if ($packageSha -notmatch '^[a-f0-9]{40}$') { throw 'Identidade do pacote inválida.' }
    $imageTag = 'ragv-homologacao:' + $packageSha.Substring(0,12)
    $metadata + @("Compose: $($settings.COMPOSE_PROJECT_NAME)","API: http://127.0.0.1:$($settings.RPY_API_PORT)","Runtime: $script:HmlRuntime") | Set-Content (Join-Path $script:HmlRoot 'diagnostics.txt') -Encoding UTF8
    $osType = Invoke-HmlDocker -Stage 'Verificando Docker Desktop (containers Linux)' -DockerArgs @('info','--format','{{.OSType}}')
    if ($osType.Trim() -ne 'linux') { throw 'Configure Docker Desktop para containers Linux.' }
    $null = Invoke-HmlDocker -Stage 'Validando configuração isolada' -DockerArgs ($compose + @('config','--quiet'))
    $images = Invoke-HmlDocker -Stage 'Verificando imagem desta revisão' -DockerArgs @('image','ls','--quiet','--filter',"reference=$imageTag")
    if ([string]::IsNullOrWhiteSpace($images)) {
        $null = Invoke-HmlDocker -Stage 'Construindo imagem (primeira abertura; pode demorar)' -DockerArgs ($compose + @('build','api'))
    }
    # Migração termina antes da API; nenhum volume/projeto habitual é selecionado.
    $null = Invoke-HmlDocker -Stage 'Iniciando PostgreSQL, migração, API e workers' -DockerArgs ($compose + @('up','-d','--no-build','postgres','migrate','api','worker-1','worker-2','scheduler'))
    $script:HmlStage = 'Aguardando API e PostgreSQL'
    $script:HmlStatusLabel.Text = $script:HmlStage
    $url = "http://127.0.0.1:$($settings.RPY_API_PORT)"
    $ready = $false
    for ($i=0; $i -lt 90; $i++) {
        try { $response = Invoke-WebRequest -Uri "$url/ready" -UseBasicParsing -TimeoutSec 2; if ($response.StatusCode -eq 200) { $ready=$true; break } } catch { }
        [Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 1000
    }
    if (-not $ready) { Write-HmlEvent $script:HmlStage 1; throw 'API/PostgreSQL não ficaram prontos no prazo. Nenhum tenant foi ignorado.' }
    # exec usa a API já saudável e seu ambiente real, não um migrate sem Bearer.
    $tenantReport = Invoke-HmlDocker -Stage 'Preparando e verificando tenant de homologação' -DockerArgs ($compose + @('exec','-T','api','python','scripts/bootstrap_homologation.py'))
    $report = $tenantReport | ConvertFrom-Json
    if ($report.status -ne 'ready' -or -not $report.tenant_exists -or $report.migration -ne '034_acquisition_authorizations.sql') { throw 'A verificação do tenant não confirmou o ambiente. Abertura cancelada.' }
    Remove-Item -LiteralPath (Join-Path $script:HmlRoot 'last-error.txt') -ErrorAction SilentlyContinue
    $script:HmlStage = 'Abrindo RagV / Grupo Villela'
    $env:RPY_DESKTOP_URL = $url
    $env:RPY_DESKTOP_CONFIGURATOR = Join-Path $PSScriptRoot 'configurar-datajud.ps1'
    $env:RPY_POWERSHELL = (Get-Command powershell.exe).Source
    $progress.Close()
    $progress = $null
    $script:HmlStatusLabel = $null
    $desktop = Start-Process -FilePath (Join-Path $PSScriptRoot 'RagVDesktop.exe') -PassThru -Wait
    if ($desktop.ExitCode -ne 0) { throw "A janela RagV encerrou com código $($desktop.ExitCode)." }
    Write-HmlEvent 'Janela encerrada normalmente' 0
} catch {
    if ($progress) { $progress.Close() }
    if (Test-Path $script:HmlRoot) { "Etapa: $script:HmlStage`r`n$($_.Exception.Message)" | Set-Content (Join-Path $script:HmlRoot 'last-error.txt') -Encoding UTF8 }
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'RagV Homologação — atenção', 'OK', 'Error') | Out-Null
    exit 1
}
