param(
    [string]$AppSha = 'a414b2a1d478ccfbf33611a7c40437dd3f90f3b7',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$PackageSha,
    [string]$Version = '0.1.2-hml'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$short = $PackageSha.Substring(0,7)
$payload = Join-Path $root '_homologation-payload'
$dist = Join-Path $root 'dist'
$installerOut = Join-Path $root 'dist-installer'
if (-not $env:RUNNER_TEMP) { $env:RUNNER_TEMP = Join-Path $env:TEMP 'ragv-homologacao-build' }
$venv = Join-Path $env:RUNNER_TEMP 'ragv-desktop-build-venv'
# Windows PowerShell 5.1 exige BOM para reconhecer UTF-8 sem depender da página de código.
foreach ($source in (Get-ChildItem (Join-Path $root 'packaging/windows') -Filter '*.ps1')) {
    $bytes = [IO.File]::ReadAllBytes($source.FullName)
    if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) { throw "Script sem UTF-8 BOM: $($source.Name)" }
}
Remove-Item $payload,$dist,$installerOut -Recurse -Force -ErrorAction SilentlyContinue
New-Item $payload,$dist,$installerOut -ItemType Directory -Force | Out-Null
$project = Join-Path $payload 'project'
New-Item $project -ItemType Directory -Force | Out-Null
$files = @('Dockerfile','.dockerignore','compose.yaml','compose.homologacao.yaml','pyproject.toml','README.md','LICENSE','NOTICE')
foreach ($file in $files) { Copy-Item (Join-Path $root $file) $project }
foreach ($dir in @('app','sql','scripts','data','requirements')) {
    $tracked = git -C $root ls-files -- $dir
    foreach ($file in $tracked) {
        $target = Join-Path $project $file
        New-Item (Split-Path $target -Parent) -ItemType Directory -Force | Out-Null
        Copy-Item (Join-Path $root $file) $target
    }
}
$overlay = Join-Path $project 'compose.homologacao.yaml'
(Get-Content $overlay -Raw -Encoding UTF8).Replace('ragv-homologacao:a414b2a1', "ragv-homologacao:$($PackageSha.Substring(0,12))") | Set-Content $overlay -Encoding utf8
Copy-Item (Join-Path $root 'packaging/windows/HOMOLOGACAO.md') (Join-Path $payload 'INSTRUCOES.md')
Set-Content (Join-Path $project 'HOMOLOGACAO.txt') "RagV Homologação`nApplication base SHA: $AppSha`nPackage SHA: $PackageSha`nPackage version: $Version" -Encoding utf8

py -3.12 -m venv $venv
$python = Join-Path $venv 'Scripts/python.exe'
& $python -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements/windows-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências fixadas do launcher.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependências de build inconsistentes.' }
Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $env:RUNNER_TEMP 'ragv-pyinstaller-work') RagVDesktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller falhou.' }
} finally { Pop-Location }
$exe = Join-Path $dist 'RagVDesktop.exe'
if (-not (Test-Path $exe)) { throw 'PyInstaller não produziu RagVDesktop.exe.' }

$iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw 'Inno Setup 6 ISCC.exe não encontrado.' }
$iss = Join-Path $root 'packaging/windows/RagVDesktop.iss'
$compilerOutput = & $iscc "/DMyAppVersion=$Version" "/DSourceSha=$short" "/O$installerOut" "/FRagVDesktop-Homologacao-$Version-$short-Setup" $iss
if ($LASTEXITCODE -ne 0) { $compilerOutput | Write-Output; throw 'Inno Setup falhou.' }
$compilerOutput | Write-Output
# ISCC.exe tem ProductVersion=0.0.0.0; a versão real é a do engine no log.
if (($compilerOutput -join "`n") -notmatch 'Compiler engine version: Inno Setup 6\.7\.3(?:\r?\n|$)') {
    Remove-Item (Join-Path $installerOut '*-Setup.exe') -Force
    throw 'Build rejeitado: engine Inno Setup diferente de 6.7.3.'
}
$outFile = Join-Path $installerOut "RagVDesktop-Homologacao-$Version-$short-Setup.exe"
if (-not (Test-Path $outFile)) { throw 'Inno Setup não produziu o instalador esperado.' }
$hash = (Get-FileHash $outFile -Algorithm SHA256).Hash.ToLowerInvariant()
@("File: $(Split-Path $outFile -Leaf)","SHA256: $hash","Application base SHA: $AppSha","Package revision: $PackageSha") | Set-Content (Join-Path $installerOut 'SHA256SUMS.txt') -Encoding ascii
Copy-Item (Join-Path $root 'packaging/windows/HOMOLOGACAO.md') $installerOut
Write-Output "INSTALLER=$outFile"
Write-Output "SHA256=$hash"
Write-Output "APP_SHA=$AppSha"
Write-Output "SIZE=$((Get-Item $outFile).Length)"
