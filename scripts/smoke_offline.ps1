$ErrorActionPreference = "Stop"
$project = "rpy-offline-smoke-$PID"
$compose = @("-f", "compose.yaml", "-f", "scripts/compose.offline.yaml", "-p", $project)
$previousPostgresPassword = $env:POSTGRES_PASSWORD
$env:POSTGRES_PASSWORD = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is required" }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker daemon is not running" }
    docker compose @compose up -d postgres
    docker compose @compose run --rm --no-deps -e ANTHROPIC_API_KEY=offline-disabled -e OPENAI_API_KEY= -e JUDIT_API_KEY= -e GEMINI_API_KEY= -e "DATABASE_URL=postgresql://rpy:$($env:POSTGRES_PASSWORD)@postgres:5432/rpy" api python scripts/smoke_offline.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch { Write-Error "offline smoke failed: $($_.Exception.Message)"; exit 1 }
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try { docker compose @compose down -v --remove-orphans *> $null } catch {}
    $ErrorActionPreference = $previousErrorActionPreference
    if ($null -eq $previousPostgresPassword) { Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue }
    else { $env:POSTGRES_PASSWORD = $previousPostgresPassword }
}
