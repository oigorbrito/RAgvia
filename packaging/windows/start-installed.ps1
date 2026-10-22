param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"

$exe = Join-Path $PSScriptRoot "RagVDesktop.exe"
$compose = Join-Path $ProjectPath "compose.yaml"

try {
    if (-not (Test-Path $exe)) {
        throw "Executavel RagVDesktop.exe nao encontrado."
    }

    if (-not (Test-Path $compose)) {
        throw "Projeto RagV nao encontrado em: $ProjectPath"
    }

    Push-Location $ProjectPath

    try {
        docker info *> $null

        if ($LASTEXITCODE -ne 0) {
            throw "Docker Desktop nao esta em execucao."
        }

        docker compose up -d --no-build

        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao iniciar os servicos RagV."
        }

        $ready = $false

        for ($i = 0; $i -lt 60; $i++) {
            try {
                $response = Invoke-WebRequest `
                    -Uri "http://127.0.0.1:8000/ready" `
                    -UseBasicParsing `
                    -TimeoutSec 3

                if ($response.StatusCode -eq 200) {
                    $ready = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 2
            }
        }

        if (-not $ready) {
            throw "A API RagV nao ficou pronta."
        }

        & $exe

        if ($LASTEXITCODE -ne 0) {
            throw "RagV Desktop terminou com erro."
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    Add-Type -AssemblyName System.Windows.Forms

    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "RagV Desktop",
        "OK",
        "Error"
    ) | Out-Null

    exit 1
}
