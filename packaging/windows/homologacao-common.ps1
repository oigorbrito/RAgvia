$ErrorActionPreference = 'Stop'
$script:HmlRoot = Join-Path $env:LOCALAPPDATA 'RagV-Homologacao'
$script:HmlRuntime = Join-Path $PSScriptRoot 'runtime'
$script:HmlEnv = Join-Path $script:HmlRoot '.env'
$script:HmlStage = 'pré-requisitos'
$script:HmlDefaultToken = 'ragv-hml-local-test-2026'

function Set-HmlPrivatePath([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    if ($item.PSIsContainer) { $acl = New-Object Security.AccessControl.DirectorySecurity; $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' }
    else { $acl = New-Object Security.AccessControl.FileSecurity; $inherit = [Security.AccessControl.InheritanceFlags]::None }
    $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid, $system)) { $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl',$inherit,'None','Allow'))) }
    $item.SetAccessControl($acl)
    $actual = $item.GetAccessControl()
    if (-not $actual.AreAccessRulesProtected) { throw 'A proteção de permissões do perfil não foi aplicada.' }
    $rules = @($actual.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw 'O perfil contém permissões inesperadas.' }
    foreach ($rule in $rules) { if ($rule.IdentityReference.Value -notin @($sid.Value,$system.Value) -or $rule.AccessControlType -ne 'Allow' -or $rule.IsInherited -or $rule.FileSystemRights -ne 'FullControl') { throw 'A verificação de permissões do perfil falhou.' } }
}

function New-HmlSecret {
    $bytes = New-Object byte[] 32; $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}
function Get-HmlHash([string]$Value, [string]$Salt) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("$Salt`:$Value")))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Show-HmlPasswordDialog([string]$Title, [string]$Prompt, [switch]$Confirmation) {
    Add-Type -AssemblyName System.Windows.Forms
    $form = New-Object Windows.Forms.Form; $form.Text=$Title; $form.Size=New-Object Drawing.Size(510,250); $form.StartPosition='CenterScreen'; $form.FormBorderStyle='FixedDialog'; $form.MaximizeBox=$false; $form.MinimizeBox=$false
    $label=New-Object Windows.Forms.Label; $label.Text=$Prompt; $label.SetBounds(20,20,450,45); $form.Controls.Add($label)
    $one=New-Object Windows.Forms.TextBox; $one.UseSystemPasswordChar=$true; $one.SetBounds(20,78,450,28); $form.Controls.Add($one)
    $two=$null
    if ($Confirmation) { $two=New-Object Windows.Forms.TextBox; $two.UseSystemPasswordChar=$true; $two.SetBounds(20,116,450,28); $form.Controls.Add($two); $two.Add_KeyDown({param($s,$e);if($e.KeyCode -eq 'Enter'){$form.DialogResult='OK';$form.Close()}}) }
    $ok=New-Object Windows.Forms.Button; $ok.Text='Continuar'; $ok.SetBounds(20,165,120,32); $form.Controls.Add($ok); $ok.Add_Click({$form.DialogResult='OK';$form.Close()})
    $cancel=New-Object Windows.Forms.Button; $cancel.Text='Cancelar'; $cancel.SetBounds(155,165,120,32); $form.Controls.Add($cancel); $cancel.Add_Click({$form.DialogResult='Cancel';$form.Close()})
    $form.AcceptButton=$ok; $form.CancelButton=$cancel; $one.Focus() | Out-Null
    if ($form.ShowDialog() -ne 'OK') { return $null }
    if ($Confirmation -and $one.Text -cne $two.Text) { throw 'As senhas não coincidem.' }
    if ($one.Text.Length -lt 8) { throw 'A senha local deve ter pelo menos 8 caracteres.' }
    return $one.Text
}
function Set-HmlPassword {
    $password = Show-HmlPasswordDialog 'RagV Homologação — criar senha local' 'Crie uma senha local. Ela desbloqueia somente esta instalação:' -Confirmation
    if ($null -eq $password) { throw 'Primeiro acesso cancelado.' }
    $salt=New-HmlSecret; $record=@{version=1;salt=$salt;hash=(Get-HmlHash $password $salt);algorithm='SHA-256';created_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json
    $path=Join-Path $script:HmlRoot 'local-password.json'; $record | Set-Content -LiteralPath $path -Encoding UTF8; Set-HmlPrivatePath $path
    $password=$null
}
function Test-HmlHash([string]$Left, [string]$Right) {
    try { $a=New-Object byte[] ($Left.Length/2);$b=New-Object byte[] ($Right.Length/2);for($j=0;$j-lt$a.Length;$j++){$a[$j]=[Convert]::ToByte($Left.Substring($j*2,2),16)};for($j=0;$j-lt$b.Length;$j++){$b[$j]=[Convert]::ToByte($Right.Substring($j*2,2),16)} } catch { return $false }
    $different=$a.Length -bxor $b.Length; $limit=[Math]::Max($a.Length,$b.Length)
    for($i=0;$i-lt$limit;$i++){ $x=if($i-lt$a.Length){$a[$i]}else{0};$y=if($i-lt$b.Length){$b[$i]}else{0};$different = $different -bor ($x -bxor $y) }
    return $different -eq 0
}
function Ensure-HmlPassword {
    $path=Join-Path $script:HmlRoot 'local-password.json'
    if (-not (Test-Path $path)) { Set-HmlPassword; return }
    $record=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.version -ne 1 -or [string]::IsNullOrWhiteSpace($record.salt) -or $record.algorithm -ne 'SHA-256') { throw 'Registro de senha local inválido; use Redefinir senha local.' }
    for ($attempt=1;$attempt -le 3;$attempt++) {
        $password=Show-HmlPasswordDialog 'RagV Homologação — desbloquear' "Informe a senha local (tentativa $attempt de 3):"
        if ($null -eq $password) { throw 'Abertura cancelada.' }
        $hash=Get-HmlHash $password ([string]$record.salt); $password=$null
        if (Test-HmlHash $hash ([string]$record.hash)) { return }
        if ($attempt -lt 3) {[Windows.Forms.MessageBox]::Show('Senha incorreta.','RagV Homologação','OK','Warning') | Out-Null}
    }
    throw 'Acesso bloqueado após três tentativas. Use Redefinir senha local.'
}
function Reset-HmlPassword {
    $path=Join-Path $script:HmlRoot 'local-password.json'; if (-not (Test-Path $path)) { Set-HmlPassword; return }
    Add-Type -AssemblyName System.Windows.Forms
    $choice=[Windows.Forms.MessageBox]::Show('A senha local será substituída. O ambiente Docker e os dados serão preservados. Continuar?','RagV Homologação — redefinir senha','YesNo','Warning')
    if ($choice -ne 'Yes') { throw 'Redefinição cancelada.' }
    Remove-Item -LiteralPath $path -Force; Set-HmlPassword
}
function Initialize-HmlPaths {
    foreach ($path in @('compose.yaml','compose.homologacao.yaml','Dockerfile','HOMOLOGACAO.txt','scripts/bootstrap_homologation.py','sql/034_acquisition_authorizations.sql')) { if (-not (Test-Path -LiteralPath (Join-Path $script:HmlRuntime $path))) { throw "Pacote instalado incompleto: runtime/$path. Reinstale a homologação." } }
    New-Item -ItemType Directory -Path $script:HmlRoot -Force | Out-Null; Set-HmlPrivatePath $script:HmlRoot
    $legacy=Join-Path $script:HmlRoot 'project/.env'; if (-not (Test-Path $script:HmlEnv) -and (Test-Path $legacy)) { $old=Get-Content $legacy; if (-not ($old -match '^COMPOSE_PROJECT_NAME=ragv-hml-[a-f0-9]{12}$')) {throw 'Perfil anterior inválido.'}; Copy-Item $legacy $script:HmlEnv }
    foreach ($file in @($script:HmlEnv,(Join-Path $script:HmlRoot 'access-token.txt'),(Join-Path $script:HmlRoot 'local-password.json'))) { if (Test-Path $file) {Set-HmlPrivatePath $file} }
    $source=(Get-Content (Join-Path $script:HmlRuntime 'compose.yaml') -Raw)+(Get-Content (Join-Path $script:HmlRuntime 'compose.homologacao.yaml') -Raw); $names=@('COMPOSE_FILE','COMPOSE_PROJECT_NAME','COMPOSE_PROFILES','COMPOSE_ENV_FILES')+@([regex]::Matches($source,'\$\{([A-Z][A-Z0-9_]*)')|ForEach-Object{$_.Groups[1].Value}); foreach($name in ($names|Select-Object -Unique)){Remove-Item "Env:$name" -ErrorAction SilentlyContinue}
}
function Get-HmlSettings {
    $settings=@{}; foreach($line in (Get-Content $script:HmlEnv)){if($line -match '^([A-Z][A-Z0-9_]*)=(.*)$'){$settings[$matches[1]]=$matches[2]}}
    if($settings.COMPOSE_PROJECT_NAME -notmatch '^ragv-hml-[a-f0-9]{12}$'){throw 'Identificador de homologação inválido.'}; $port=0;if(-not[int]::TryParse($settings.RPY_API_PORT,[ref]$port)-or$port-lt1024-or$port-gt65535){throw 'Porta local inválida.'}
    try{$mapping=$settings.RPY_BEARER_TOKENS|ConvertFrom-Json;$entries=@($mapping.PSObject.Properties);$tenant=[guid]::Empty;if($entries.Count-ne 1-or-not[guid]::TryParse([string]$entries[0].Value,[ref]$tenant)){throw 'invalid'}}catch{throw 'Bearer/tenant local inválido.'}
    if($entries[0].Name -cne $script:HmlDefaultToken){
        $settings.RPY_BEARER_TOKENS = (@{$script:HmlDefaultToken=$tenant.ToString()} | ConvertTo-Json -Compress)
        $lines=Get-Content -LiteralPath $script:HmlEnv | Where-Object {$_ -notmatch '^RPY_BEARER_TOKENS='}
        @($lines + "RPY_BEARER_TOKENS=$($settings.RPY_BEARER_TOKENS)") | Set-Content -LiteralPath $script:HmlEnv -Encoding ASCII
        Set-HmlPrivatePath $script:HmlEnv
    }
    $tokenFile=Join-Path $script:HmlRoot 'access-token.txt';if((Test-Path $tokenFile)-and((Get-Content $tokenFile -Raw).Trim()-cne$script:HmlDefaultToken)){Set-Content $tokenFile $script:HmlDefaultToken -Encoding ASCII;Set-HmlPrivatePath $tokenFile};return $settings
}
function Write-HmlEvent([string]$Stage,[int]$Code){$event=@{at=[DateTime]::UtcNow.ToString('o');stage=$Stage;exit_code=$Code;ps_version=$PSVersionTable.PSVersion.ToString()};Add-Content (Join-Path $script:HmlRoot 'startup.jsonl') ($event|ConvertTo-Json -Compress) -Encoding UTF8}
function Invoke-HmlDocker([string]$Stage,[string[]]$DockerArgs){$script:HmlStage=$Stage;if($script:HmlStatusLabel){$script:HmlStatusLabel.Text=$Stage;[Windows.Forms.Application]::DoEvents()};$start=New-Object Diagnostics.ProcessStartInfo;$start.FileName=(Get-Command docker.exe).Source;$start.Arguments=(($DockerArgs|ForEach-Object{'"'+$_.Replace('"','\"')+'"'})-join' ');$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true;$p=New-Object Diagnostics.Process;$p.StartInfo=$start;$p.Start()|Out-Null;$stdout=$p.StandardOutput.ReadToEndAsync();$stderr=$p.StandardError.ReadToEndAsync();while(-not$p.WaitForExit(100)){if($script:HmlStatusLabel){[Windows.Forms.Application]::DoEvents()}};$out=$stdout.GetAwaiter().GetResult();$null=$stderr.GetAwaiter().GetResult();$code=$p.ExitCode;$p.Dispose();Write-HmlEvent $Stage $code;if($code-ne 0){throw "A etapa '$Stage' falhou (código $code). Veja startup.jsonl."};return $out}
function Get-HmlComposeArgs([hashtable]$Settings){return @('compose','--project-name',$Settings.COMPOSE_PROJECT_NAME,'--project-directory',$script:HmlRuntime,'--env-file',$script:HmlEnv,'-f',(Join-Path $script:HmlRuntime 'compose.yaml'),'-f',(Join-Path $script:HmlRuntime 'compose.homologacao.yaml'))}
