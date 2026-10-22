$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$path = Join-Path $env:LOCALAPPDATA 'RagV-Homologacao\access-token.txt'
if (-not (Test-Path $path)) {
    [System.Windows.Forms.MessageBox]::Show('Abra RagV Homologação primeiro para criar o token local.', 'RagV Homologação') | Out-Null
    exit 1
}
Set-Clipboard -Value ((Get-Content -LiteralPath $path -Raw).Trim())
[System.Windows.Forms.MessageBox]::Show('Token de homologação copiado. Cole em Configurar acesso > Bearer token e clique em Verificar. Não compartilhe o token. Limpe a área de transferência após o uso.', 'RagV Homologação') | Out-Null
