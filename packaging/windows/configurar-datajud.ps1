$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'homologacao-common.ps1')
Add-Type -AssemblyName System.Windows.Forms
$script:DataJudOfficialUrl = 'https://datajud-wiki.cnj.jus.br/api-publica/acesso/'

function Get-OfficialDataJudApiKey {
    $uri = [Uri]$script:DataJudOfficialUrl
    if ($uri.Scheme -cne 'https' -or $uri.Host -cne 'datajud-wiki.cnj.jus.br' -or $uri.AbsolutePath -cne '/api-publica/acesso/') {
        throw 'Fonte oficial DataJud inválida.'
    }
    try {
        $response = Invoke-WebRequest -Uri $uri.AbsoluteUri -UseBasicParsing -TimeoutSec 20
    } catch {
        throw 'Não foi possível obter a chave pública da documentação oficial do CNJ.'
    }
    $finalUri = [Uri]$response.BaseResponse.ResponseUri
    if ($finalUri.Scheme -cne 'https' -or $finalUri.Host -cne 'datajud-wiki.cnj.jus.br') {
        throw 'A fonte da chave pública redirecionou para um domínio não autorizado.'
    }
    $pattern = '(?is)Authorization:\s*APIKey\s*(?:<strong>\s*)?([A-Za-z0-9_+/=:-]{16,2048})'
    $match = [regex]::Match([string]$response.Content, $pattern)
    if (-not $match.Success) { throw 'A documentação oficial não publicou uma chave pública reconhecível.' }
    return $match.Groups[1].Value.Trim()
}

function Wait-DataJudCapabilities([hashtable]$Settings) {
    $url = "http://127.0.0.1:$($Settings.RPY_API_PORT)/capabilities"
    $lastReason = 'o backend não confirmou o DataJud'
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $url -Headers @{ Authorization = 'Bearer ' + $script:HmlDefaultToken } -UseBasicParsing -TimeoutSec 3
            $capabilities = $response.Content | ConvertFrom-Json
            $datajud = $capabilities.sources.datajud
            if ($datajud.enabled -eq $true) { return $datajud }
            if ($datajud.reason_unavailable) { $lastReason = [string]$datajud.reason_unavailable }
        } catch { }
        Start-Sleep -Seconds 1
    }
    throw "DataJud não foi confirmado pelo backend após a ativação: $lastReason"
}

try {
    Initialize-HmlPaths
    if (-not (Test-Path $script:HmlEnv)) { throw 'Abra o RagV Homologação uma vez antes de configurar o DataJud.' }
    $settings = Get-HmlSettings
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Configuração DataJud — RagV Homologação'
    $form.Size = New-Object Drawing.Size(640,330)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $link = New-Object Windows.Forms.LinkLabel
    $link.Text = 'Fonte oficial: datajud-wiki.cnj.jus.br — obter chave automaticamente'
    $link.SetBounds(20,20,580,30)
    $link.Add_LinkClicked({ Start-Process $script:DataJudOfficialUrl })
    $form.Controls.Add($link)
    $label = New-Object Windows.Forms.Label
    $label.Text = 'Cole a chave vigente. Nenhuma consulta será enviada pela configuração.'
    $label.SetBounds(20,60,580,35)
    $form.Controls.Add($label)
    $keyBox = New-Object Windows.Forms.TextBox
    $keyBox.UseSystemPasswordChar = $true
    $keyBox.ReadOnly = $true
    $keyBox.SetBounds(20,100,430,26)
    $form.Controls.Add($keyBox)
    $fetch = New-Object Windows.Forms.Button
    $fetch.Text = 'Obter chave oficial'
    $fetch.SetBounds(460,98,140,30)
    $form.Controls.Add($fetch)
    $fetch.Add_Click({
        try {
            $fetch.Enabled = $false
            $status.Text = 'Consultando somente a documentação oficial do CNJ…'
            $keyBox.Text = Get-OfficialDataJudApiKey
            $status.Text = 'Chave vigente obtida. Confirme o uso autorizado para ativar.'
        } catch {
            $keyBox.Clear()
            $status.Text = $_.Exception.Message
        } finally { $fetch.Enabled = $true }
    })
    $optin = New-Object Windows.Forms.CheckBox
    $optin.Text = 'Confirmo finalidade e uso autorizados pelas regras vigentes do CNJ.'
    $optin.SetBounds(20,145,580,35)
    $form.Controls.Add($optin)
    $status = New-Object Windows.Forms.Label
    $status.SetBounds(20,190,580,30)
    $form.Controls.Add($status)
    $save = New-Object Windows.Forms.Button
    $save.Text = 'Salvar e ativar'
    $save.SetBounds(20,235,150,32)
    $form.Controls.Add($save)
    $save.Add_Click({
        try {
            if (-not $optin.Checked) { throw 'Confirme o uso autorizado antes de ativar.' }
            $key = $keyBox.Text.Trim() -replace '^APIKey\s+', ''
            if ($key -notmatch '^[A-Za-z0-9_+/=:-]{16,2048}$') { throw 'Chave vazia ou formato inválido. Confira a fonte oficial do CNJ.' }
            $save.Enabled = $false
            $lines = @(Get-Content -LiteralPath $script:HmlEnv -Encoding UTF8)
            $new = @($lines | Where-Object { $_ -notmatch '^DATAJUD_(API_KEY|ENABLED|AUTHORIZED_USE)=' })
            $new += @('DATAJUD_ENABLED=true','DATAJUD_AUTHORIZED_USE=true',"DATAJUD_API_KEY=$key")
            $tmp = "$($script:HmlEnv).new"
            $new | Set-Content -LiteralPath $tmp -Encoding ASCII
            Set-HmlPrivatePath $tmp
            Move-Item -LiteralPath $tmp -Destination $script:HmlEnv -Force
            $key = $null
            $keyBox.Clear()
            $script:HmlStatusLabel = $status
            $compose = Get-HmlComposeArgs $settings
            $null = Invoke-HmlDocker -Stage 'Aplicando configuração DataJud autorizada' -DockerArgs ($compose + @('up','-d','--no-build','--no-deps','--force-recreate','api','worker-1','worker-2','scheduler'))
            $script:HmlStatusLabel = $null
            $null = Wait-DataJudCapabilities $settings
            $status.Text = 'DataJud confirmado pela API. Nenhuma consulta enviada.'
            [Windows.Forms.MessageBox]::Show('DataJud ativado e confirmado pela API. A chave fica somente nos workers. Nenhuma consulta foi enviada.', 'Configuração concluída', 'OK', 'Information') | Out-Null
            $form.Close()
        } catch {
            $save.Enabled = $true
            [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Configuração DataJud — atenção', 'OK', 'Error') | Out-Null
        }
    })
    [void]$form.ShowDialog()
} catch {
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'RagV Homologação — atenção', 'OK', 'Error') | Out-Null
    exit 1
}
