$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'homologacao-common.ps1')
try { Initialize-HmlPaths; Reset-HmlPassword; [Windows.Forms.MessageBox]::Show('Senha local redefinida. Abra o RagV Homologação e use a nova senha.','RagV Homologação','OK','Information') | Out-Null }
catch { Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.MessageBox]::Show($_.Exception.Message,'RagV Homologação — atenção','OK','Error') | Out-Null; exit 1 }
