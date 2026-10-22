param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [Parameter(Mandatory = $true)]
    [string]$CurrentVersion
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message) { throw "Atualização rejeitada: $Message" }

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { Fail 'manifesto ausente.' }
try { $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail 'manifesto JSON inválido.' }

foreach ($name in @('version', 'installer_path', 'sha256', 'publisher')) {
    if ([string]::IsNullOrWhiteSpace([string]$manifest.$name)) { Fail "campo obrigatório ausente: $name." }
}
if ([string]$manifest.version -le $CurrentVersion) { Fail 'a versão disponível não é mais nova.' }
if ([string]$manifest.sha256 -notmatch '^[A-Fa-f0-9]{64}$') { Fail 'SHA-256 inválido.' }
if ([string]$manifest.publisher -ne 'RagV / Grupo Villela') { Fail 'publisher não permitido.' }

$installer = [IO.Path]::GetFullPath([string]$manifest.installer_path)
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { Fail 'instalador ausente.' }
$actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
$left = $actualHash.ToUpperInvariant(); $right = ([string]$manifest.sha256).ToUpperInvariant()
$difference = $left.Length -bxor $right.Length
for ($i = 0; $i -lt [Math]::Max($left.Length, $right.Length); $i++) {
    $a = if ($i -lt $left.Length) { [int][char]$left[$i] } else { 0 }
    $b = if ($i -lt $right.Length) { [int][char]$right[$i] } else { 0 }
    $difference = $difference -bor ($a -bxor $b)
}
if ($difference -ne 0) { Fail 'hash do instalador não corresponde ao manifesto.' }

$signature = Get-AuthenticodeSignature -FilePath $installer
if ($signature.Status -ne 'Valid') { Fail 'instalador sem assinatura Authenticode válida.' }
$fileVersion = (Get-Item -LiteralPath $installer).VersionInfo.ProductVersion
if ([string]::IsNullOrWhiteSpace($fileVersion) -or $fileVersion -ne [string]$manifest.version) { Fail 'versão do arquivo não corresponde ao manifesto.' }
if (-not $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch [regex]::Escape([string]$manifest.publisher)) {
    Fail 'certificado do instalador não corresponde ao publisher permitido.'
}

[pscustomobject]@{ version = [string]$manifest.version; installer = $installer; signature = 'Valid'; verified = $true } | ConvertTo-Json -Compress
