param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$backupFullPath = [System.IO.Path]::GetFullPath($BackupPath)
$backupDirectory = Split-Path -Parent $backupFullPath
$backupName = Split-Path -Leaf $backupFullPath

if (-not (Test-Path $backupDirectory)) {
    throw "O diretório de backup aprovado não existe: $backupDirectory"
}
if (Test-Path $backupFullPath) {
    throw "O destino do backup já existe; escolha um caminho novo: $backupFullPath"
}

Push-Location $repo
try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop não está em execução." }

    # Reutiliza o script de backup testado dentro da imagem PostgreSQL, sem
    # transferir dados do volume para uma imagem da aplicação.
    $repoMount = "${repo}:/work:ro"
    $backupMount = "${backupDirectory}:/backup"
    docker compose run --rm --no-deps -v $repoMount -v $backupMount --entrypoint sh postgres -c 'DATABASE_URL="postgresql://rpy:$POSTGRES_PASSWORD@postgres:5432/rpy" sh /work/scripts/backup_database.sh "/backup/$1"' sh $backupName
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $backupFullPath, "$backupFullPath.sha256" -Force -ErrorAction SilentlyContinue
        throw "Backup ou validação falhou; atualização cancelada."
    }

    # Um único conjunto versionado: migração, API (inclui frontend), workers e scheduler.
    docker compose build migrate api worker-1 worker-2 scheduler
    if ($LASTEXITCODE -ne 0) { throw "Falha ao construir o conjunto coordenado; serviços não foram atualizados." }

    docker compose up -d --no-build migrate api worker-1 worker-2 scheduler
    if ($LASTEXITCODE -ne 0) { throw "Falha ao iniciar o conjunto coordenado RagV." }
}
finally {
    Pop-Location
}
