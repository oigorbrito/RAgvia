$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $repo "dist\RagVDesktop.exe"

if (-not (Test-Path $exe)) {
    throw "RagVDesktop.exe não encontrado. Compile primeiro."
}

Push-Location $repo

try {
    docker info *> $null

    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop não está em execução."
    }

    # A abertura cotidiana nunca reconstrói imagens nem inicia uma atualização.
    # Atualizações coordenadas são explícitas via scripts/update-ragv-desktop.ps1.
    docker compose up -d --no-build
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao iniciar os serviços RagV."
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
        throw "API RagV não ficou pronta dentro do prazo."
    }

    & $exe
}
finally {
    Pop-Location
}
