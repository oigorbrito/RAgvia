"""Smoke opt-in do instalador real: Windows/Docker/WebView2, sem consulta externa.

Uso: python tests/windows_installer_smoke.py --installer <exe> --install-dir <pasta>
     --evidence-dir <pasta>
Requer playwright e Pillow no ambiente de teste, não no computador do usuário final.
Recusa perfil já inicializado: preserve-o explicitamente antes de um teste limpo.
"""
import argparse
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4

from playwright.async_api import async_playwright

HABITUAL = ['ragv-api-1', 'ragv-worker-1-1', 'ragv-worker-2-1', 'ragv-scheduler-1', 'ragv-postgres-1']
ROOT = Path(os.environ['LOCALAPPDATA']) / 'RagV-Homologacao'
PS = str(Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, encoding='utf-8', errors='strict', creationflags=NO_WINDOW, **kwargs)


def powershell(code):
    return run([PS, '-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ' + code])


def snapshot():
    rows = json.loads(run(['docker', 'inspect', *HABITUAL]))
    return [{key: value for key, value in {'name': row['Name'], 'id': row['Id'], 'image': row['Image'], 'started': row['State']['StartedAt']}.items()} for row in rows]


def profile():
    return dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line)


def api_get(url, token):
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def wait_ready():
    for _ in range(900):
        if (ROOT / '.env').exists():
            settings = profile()
            url = f"http://127.0.0.1:{settings['RPY_API_PORT']}"
            try:
                with urllib.request.urlopen(url + '/ready', timeout=2) as response:
                    if response.status == 200:
                        return settings, url
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
        if (ROOT / 'last-error.txt').exists():
            raise RuntimeError('Falha registrada em last-error.txt; o smoke não aceita continuar')
        time.sleep(1)
    raise TimeoutError('Inicialização não terminou em 900 segundos')


def close_window(install):
    powershell(f"Get-Process RagVDesktop -ErrorAction SilentlyContinue | Where-Object {{$_.Path -eq '{install / 'RagVDesktop.exe'}' -and $_.MainWindowHandle -ne 0}} | ForEach-Object {{$_.CloseMainWindow() | Out-Null}}; exit 0")
    time.sleep(3)


async def inspect_native(link, install, url, token, evidence, label, expected_history):
    env = os.environ.copy()
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--remote-debugging-port=19267'
    env['WEBVIEW2_USER_DATA_FOLDER'] = str(evidence / 'webview-test-profile')
    subprocess.Popen('"' + link['target'] + '" ' + link['args'], env=env, creationflags=NO_WINDOW)
    async with async_playwright() as playwright:
        browser = None
        for _ in range(150):
            try:
                browser = await playwright.chromium.connect_over_cdp('http://127.0.0.1:19267')
                break
            except Exception:
                await asyncio.sleep(1)
        if not browser:
            raise RuntimeError('WebView2 instalado não abriu')
        page = None
        for _ in range(60):
            pages = [page for context in browser.contexts for page in context.pages if page.url.startswith(url)]
            if pages:
                page = pages[0]
                break
            await asyncio.sleep(1)
        assert page is not None
        await page.locator('.brand').wait_for()
        assert (await page.locator('.brand').inner_text()).strip() == 'RagV'
        assert 'grupo villela' in (await page.locator('.client-reference').inner_text()).lower()
        assert 'Configurar acesso' in await page.locator('#access-label').inner_text()
        await page.screenshot(path=str(evidence / f'{label}-janela-nativa.png'))
        await page.locator('#access-trigger').click()
        await page.locator('#bearer-token').fill(token)
        await page.locator('#verify-token').click()
        await page.locator('#access-trigger.authenticated').wait_for(timeout=20000)
        await page.locator('#history-trigger').click()
        await page.locator('#history-panel').wait_for(state='visible')
        if expected_history:
            await page.get_by_text(expected_history, exact=False).first.wait_for()
        await page.locator('#history-panel').screenshot(path=str(evidence / f'{label}-historico.png'))
        close_window(install)


def configuration_dialog(evidence):
    code = "Get-ChildItem (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs') -Filter 'Configurar DataJud - RagV Homolog*.lnk' | Select-Object -First 1 -ExpandProperty FullName"
    os.startfile(powershell(code).strip())
    user32 = ctypes.windll.user32
    titles = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def collect(hwnd, _):
        buf = ctypes.create_unicode_buffer(2048)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        if user32.IsWindowVisible(hwnd):
            titles.append((hwnd, buf.value))
        return True
    expected = 'Configuração DataJud — RagV Homologação'
    hwnd = None
    for _ in range(60):
        titles.clear()
        user32.EnumWindows(collect, 0)
        matches = [handle for handle, title in titles if title == expected]
        if matches:
            hwnd = matches[0]
            break
        time.sleep(1)
    assert hwnd, 'Diálogo de configuração ausente ou com codificação incorreta'
    labels = []
    @callback_type
    def children(handle, _):
        buf = ctypes.create_unicode_buffer(2048)
        user32.GetWindowTextW(handle, buf, len(buf))
        labels.append(buf.value)
        return True
    user32.EnumChildWindows(hwnd, children, 0)
    assert 'Cole a chave vigente. Nenhuma consulta será enviada pela configuração.' in labels
    output = str(evidence / 'configuracao-datajud-acentos.png').replace("'", "''")
    powershell(f'''Add-Type -AssemblyName System.Drawing;
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class Captura196C {{ [StructLayout(LayoutKind.Sequential)] public struct R {{public int L,T,D,B;}} [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out R r);[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);}}';
$r=New-Object Captura196C+R;[Captura196C]::GetWindowRect([IntPtr]{hwnd},[ref]$r)|Out-Null;
$b=New-Object Drawing.Bitmap(($r.D-$r.L),($r.B-$r.T));$g=[Drawing.Graphics]::FromImage($b);$dc=$g.GetHdc();$ok=[Captura196C]::PrintWindow([IntPtr]{hwnd},$dc,2);$g.ReleaseHdc($dc);if(-not $ok){{throw 'Captura falhou'}};$b.Save('{output}',[Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$b.Dispose()''')
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    return {'titulo': expected, 'texto': labels, 'configuracao_acessivel': True, 'consulta_enviada': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    assert not (ROOT / '.env').exists(), 'Perfil existente: preserve-o antes de executar o smoke limpo'
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    # subprocess lista preserva corretamente /DIR com espaços, sem shell.
    installed = subprocess.run([str(args.installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/DIR={args.install_dir}'], creationflags=NO_WINDOW)
    assert installed.returncode == 0
    for file in ['RagVDesktop.exe', 'homologacao-common.ps1', 'runtime/compose.yaml', 'runtime/scripts/bootstrap_homologation.py', 'runtime/sql/034_acquisition_authorizations.sql']:
        assert (args.install_dir / file).is_file(), file
    link = json.loads(powershell("Get-ChildItem (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs') -Filter 'RagV Homolog*.lnk' | Select-Object -First 1 | ForEach-Object {$s=(New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName); @{path=$_.FullName;target=$s.TargetPath;args=$s.Arguments}|ConvertTo-Json -Compress}"))
    os.startfile(link['path'])  # Abertura real pela interface/atalho instalado.
    settings, url = wait_ready()
    for _ in range(120):
        windows = powershell(f"Get-Process RagVDesktop -ErrorAction SilentlyContinue | Where-Object {{$_.Path -eq '{args.install_dir / 'RagVDesktop.exe'}' -and $_.MainWindowHandle -ne 0}} | Select-Object -ExpandProperty MainWindowTitle; exit 0")
        if 'RagV' in windows:
            break
        time.sleep(1)
    else:
        raise AssertionError('Atalho não abriu a janela nativa')
    close_window(args.install_dir)
    token = (ROOT / 'access-token.txt').read_text().strip()
    tenant = next(iter(json.loads(settings['RPY_BEARER_TOKENS']).values()))
    name = settings['COMPOSE_PROJECT_NAME']
    postgres = f'{name}-postgres-1'
    sql = "SELECT json_build_object('id',id,'created_at',created_at,'name',name) FROM tenants WHERE id='%s'" % tenant
    tenant_before = json.loads(run(['docker','exec',postgres,'psql','-U','rpy','-d','rpy','-Atc',sql]))
    # Fixture explícita SOMENTE de persistência; não representa consulta nem provedor real.
    fixture = 'Registro sintético — persistência 196C'
    payload = json.dumps({'tenant_id':tenant,'code':'0000000-00.2024.8.26.0107','operation':'metadata_lookup','purpose':'public_metadata_lookup','classification_at_request':'unknown'})
    result = json.dumps({'status':'ok','metadata':{'class_name':fixture}})
    insert = "INSERT INTO jobs(id,task_name,status,payload,result) VALUES ('%s','lookup_datajud_metadata','completed','%s','%s');" % (uuid4(), payload, result)
    run(['docker','exec','-i',postgres,'psql','-U','rpy','-d','rpy','-v','ON_ERROR_STOP=1'], input=insert)
    history = api_get(url + '/processes/datajud/history', token)
    assert len(history['items']) == 1
    asyncio.run(inspect_native(link,args.install_dir,url,token,args.evidence_dir,'primeira',fixture))
    # Os eventos contam builds; reabertura deve manter o tenant e a fixture.
    def builds():
        return sum('Construindo imagem' in line for line in (ROOT / 'startup.jsonl').read_text(encoding='utf-8-sig').splitlines())
    first_builds = builds()
    asyncio.run(inspect_native(link,args.install_dir,url,token,args.evidence_dir,'reabertura',fixture))
    assert builds() == first_builds == 1
    assert api_get(url + '/processes/datajud/history', token) == history
    assert json.loads(run(['docker','exec',postgres,'psql','-U','rpy','-d','rpy','-Atc',sql])) == tenant_before
    rows = json.loads(run(['docker','inspect',*[f'{name}-{s}-1' for s in ('api','worker-1','worker-2','scheduler','postgres')]]))
    assert all(row['State']['Running'] for row in rows)
    assert rows[-1]['State']['Health']['Status'] == 'healthy'
    migration = run(['docker','exec',postgres,'psql','-U','rpy','-d','rpy','-Atc','SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 1']).strip()
    assert migration == '034_acquisition_authorizations.sql'
    assert len({row['Image'] for row in rows[:-1]}) == 1
    assert not rows[-1]['NetworkSettings']['Ports'].get('5432/tcp')
    assert all(mount.get('Name') != 'ragv_rpy_pgdata' for mount in rows[-1]['Mounts'])
    # Exercita a falha C original, sem mudar banco: mapeamento ausente precisa sair com 2.
    negative = subprocess.run(['docker','exec','-e','RPY_BEARER_TOKENS=',f'{name}-api-1','python','scripts/bootstrap_homologation.py'], capture_output=True, text=True, creationflags=NO_WINDOW)
    assert negative.returncode == 2 and json.loads(negative.stdout)['status'] == 'invalid_tenant_configuration'
    dialog = configuration_dialog(args.evidence_dir)
    assert snapshot() == before
    elevated = powershell("$p=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()); $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)").strip()
    (args.evidence_dir/'startup.jsonl').write_bytes((ROOT/'startup.jsonl').read_bytes())
    (args.evidence_dir/'installed-revision.txt').write_bytes((args.install_dir/'runtime/HOMOLOGACAO.txt').read_bytes())
    report = {'migration':migration,'postgres_health':'healthy','installer': args.installer.name,'sha256':hashlib.sha256(args.installer.read_bytes()).hexdigest(),'bytes':args.installer.stat().st_size,'install_exit':installed.returncode,'windows_install':'PASS','shortcut_native_window':'PASS','reopening':'PASS','build_count':first_builds,'tenant_persisted':'PASS','synthetic_history_persisted':'PASS','fixture_scope':'Persistência somente; nenhuma consulta processual enviada','bootstrap_missing_mapping_exit':negative.returncode,'same_image_api_workers_scheduler':'PASS','habitual_isolation':'PASS','habitual_before':before,'habitual_after':snapshot(),'elevated':elevated,'install_directory':str(args.install_dir),'api_port':settings['RPY_API_PORT'],'compose_project':name,'provider_queries':0,'dialogo_codificacao':dialog}
    (args.evidence_dir/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
