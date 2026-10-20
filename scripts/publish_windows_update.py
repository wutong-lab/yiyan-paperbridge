"""Build model-free Windows updates and publish the verified update channel."""
import argparse
import base64
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import publish_offline_ci as common
from package_windows import copy,digest

CONFIG=json.loads((ROOT/'release-assets/windows-version.json').read_text())
VERSION=CONFIG['version'];TAG=f'v{VERSION}-windows-update'
OUT=ROOT/'dist/windows-update';APP=OUT/'Yiyan';HELPER=ROOT/'.cache/windows-update-build'
BASE=f'https://github.com/{common.DOWNLOAD_REPO}/releases/download/{TAG}/'

def publish(project,folder,version=VERSION):
    subprocess.run(['dotnet','publish',str(ROOT/project),'-c','Release','-r','win-x64','--self-contained','true','-p:PublishSingleFile=true','-p:IncludeNativeLibrariesForSelfExtract=true','-p:EnableCompressionInSingleFile=true',f'-p:Version={version}','-o',str(folder)],check=True)

def build():
    if sys.platform!='win32':raise RuntimeError('Build Windows binaries on Windows x64')
    OUT.mkdir(parents=True,exist_ok=True);HELPER.mkdir(parents=True,exist_ok=True)
    if not (ROOT/'examples/sci-layout-sample.pdf').is_file():
        subprocess.run([sys.executable,str(ROOT/'scripts/make_sample.py')],check=True)
    publish('desktop/windows-update/Updater.csproj',HELPER)
    with (HELPER/'Yiyan-Updater.exe').open('rb') as src,gzip.open(HELPER/'Yiyan-Updater.exe.gz','wb',compresslevel=9) as dst:shutil.copyfileobj(src,dst)
    publish('desktop/windows/PaperBridge.csproj',APP)
    copy(HELPER/'Yiyan-Updater.exe',APP/'Updater/Yiyan-Updater.exe')
    engine=APP/'Engine'
    for name in ['paperbridge','scripts','desktop','tests','docs','examples','LICENSE','THIRD_PARTY_NOTICES.md','README.md','requirements.txt','requirements.lock.txt','requirements.windows.txt']:
        copy(ROOT/name,engine/name)
    assets=engine/'release-assets';assets.mkdir(parents=True,exist_ok=True)
    for item in (ROOT/'release-assets').iterdir():
        if item.name not in ['vendor-sources','windows-licenses']:
            copy(item,assets/item.name)
    copy(ROOT/'docs/WINDOWS_INSTALL.md',APP/'Windows-安装教程.md')
    (APP/'BUILD-REPORT.json').write_text(json.dumps({'version':VERSION,'platform':'Windows x64','models':'preserve_installed_4B_and_14B','runtime_reused':not CONFIG['include_runtime'],'source_commit':os.environ.get('GITHUB_SHA')},indent=2),encoding='utf-8')
    if CONFIG['include_runtime']:
        # Future dependency updates can supply a separately built, pinned runtime.
        runtime=os.environ.get('PAPERBRIDGE_WINDOWS_RUNTIME_SOURCE')
        if not runtime or not Path(runtime).is_dir():raise RuntimeError('Provide a verified runtime source for include_runtime=true')
        copy(Path(runtime),APP/'Runtime')
    components=sorted(p.name for p in APP.iterdir());allowed={'Engine','Updater','Runtime','Yiyan.exe','Yiyan.pdb','Yiyan.dll','Yiyan.deps.json','Yiyan.runtimeconfig.json','WebView2Loader.dll','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','Microsoft.Web.WebView2.Wpf.dll','Windows-安装教程.md','BUILD-REPORT.json'}
    allowed.update({'Microsoft.Web.WebView2.Core.xml','Microsoft.Web.WebView2.WinForms.xml','Microsoft.Web.WebView2.Wpf.xml'})
    assert set(components)<=allowed, 'Unexpected application components: '+repr(sorted(set(components)-allowed))
    archive=OUT/f'Yiyan-{VERSION}-Windows-x64-App.zip';expanded=0
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for p in sorted(APP.rglob('*')):
            if p.is_file():
                assert p.suffix.lower() not in ['.gguf','.safetensors'] and 'Models' not in p.relative_to(APP).parts
                expanded+=p.stat().st_size;z.write(p,'Yiyan/'+p.relative_to(APP).as_posix())
    manifest={'schema':1,'version':VERSION,'platform':'windows-x64','archive':{'name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),'url':BASE+archive.name},'components':components,'expanded_bytes':expanded,'minimum_installed_version':CONFIG['minimum_installed_version'],'runtime_included':CONFIG['include_runtime']}
    (OUT/'windows-app.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    publish('desktop/windows-setup/Setup.csproj',OUT/'Setup')
    shutil.copy2(HELPER/'Yiyan-Updater.exe',OUT/'Yiyan-Updater.exe');shutil.copy2(OUT/'Setup/Yiyan-Windows-OneClick-Setup.exe',OUT/'Yiyan-Windows-OneClick-Setup.exe')
    # A controlled older launcher exercises the actual in-app update menu in CI.
    publish('desktop/windows/PaperBridge.csproj',OUT/'TestLauncher',version='1.8.9')
    source=OUT/f'PaperBridge-{VERSION}-Windows-Source.zip'
    subprocess.run(['git','archive','--format=zip',f'--prefix=PaperBridge-{VERSION}/','--output',str(source),'HEAD'],cwd=ROOT,check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/verify_publication.py'),'--archive',str(source)],check=True)
    report={'version':VERSION,'source_commit':os.environ.get('GITHUB_SHA'),'archive_bytes':archive.stat().st_size,'models_in_archive':False,'runtime_included':CONFIG['include_runtime'],'expanded_bytes':expanded,'components':components}
    (OUT/'WINDOWS-UPDATE-BUILD-REPORT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

def find_release():
    return next((x for x in common.api(f'/repos/{common.REPO}/releases?per_page=100') if x['tag_name']==TAG or '<!-- windows-update: '+TAG+' -->' in (x.get('body') or '')),None)

def candidate():
    common.require_runner();r=find_release()
    if r and not r['draft']:raise RuntimeError('Published releases are immutable')
    if not r:r=common.api(f'/repos/{common.REPO}/releases',{'tag_name':TAG,'target_commitish':os.environ['GITHUB_SHA'],'name':'译研 · Windows 一键安装与应用更新 · 4B / 14B','body':'<!-- windows-update: '+TAG+' -->','draft':True,'prerelease':True})
    for p in [*OUT.glob('*.*'),*OUT.glob('screenshots/*.png')]:
        # Only an unpublished draft can replace failed-build assets. Public
        # candidates are immutable and must use a new version for any fix.
        for asset in common.api(f"/repos/{common.REPO}/releases/{r['id']}/assets?per_page=100"):
            if asset['name']==p.name and (asset.get('digest')!='sha256:'+digest(p) or asset['size']!=p.stat().st_size):
                try:common.api(f"/repos/{common.REPO}/releases/assets/{asset['id']}",method='DELETE')
                except json.JSONDecodeError:pass  # Successful deletion returns 204 with no JSON body.
        common.upload(r['id'],p)
    body=(ROOT/'docs/RELEASE_WINDOWS_UPDATE.md').read_text(encoding='utf-8').replace('{VERSION}',VERSION)
    common.api(f"/repos/{common.REPO}/releases/{r['id']}",{'tag_name':TAG,'target_commitish':os.environ['GITHUB_SHA'],'draft':False,'prerelease':True,'make_latest':'false','body':body},method='PATCH')

def promote():
    common.require_runner();r=find_release();assert r and not r['draft']
    assets={a['name']:a for a in r['assets']};entry=assets['windows-app.json'];path=ROOT/'.cache/windows-app.json'
    common.download(entry['browser_download_url'],path,entry['digest'].removeprefix('sha256:'),entry['size']);manifest=json.loads(path.read_text())
    archive=assets[manifest['archive']['name']];assert archive['digest']=='sha256:'+manifest['archive']['sha256'] and archive['size']==manifest['archive']['bytes']
    evidence=ROOT/'validation/WINDOWS-UPDATE-PRESERVATION.json';report=json.loads(evidence.read_text(encoding='utf-8-sig'))
    for field in ['native_menu_triggered','model_sha256_unchanged','model_timestamp_unchanged','papers_and_settings_unchanged','runtime_hashes_unchanged','offline_first_launch_passed','model_not_downloaded_during_update']:assert report[field] is True,field
    assert report['version']==VERSION
    pdf=json.loads((ROOT/'validation/WINDOWS-OFFLINE-SMOKE-REPORT.json').read_text(encoding='utf-8-sig'));assert pdf['status']=='complete' and pdf['downloads_forbidden'] and pdf['structure_passed']
    api_report=ROOT/'validation/WINDOWS-API-HARDWARE-REPORT.json'
    api=json.loads(api_report.read_text(encoding='utf-8-sig'))
    assert api['passed'] and api['native_dpapi_roundtrip'] and api['reopened_profile_passed'] and not api['user_credential_used']
    if CONFIG.get('require_table_translation_validation'):
        table_report=ROOT/'validation/TABLE-TRANSLATION-REPORT.json'
        tables=json.loads(table_report.read_text(encoding='utf-8-sig'))
        assert tables['passed'] and tables['version']==VERSION and tables['native_version']==VERSION
        assert tables['actual_installed_runtime'] and tables['real_4b_inference']
        assert not tables['development_source_override'] and not tables['user_credential_used']
        assert {case['case'] for case in tables['cases']}=={'off','on'}
        assert all(case['passed'] and case['status']=='complete' for case in tables['cases'])
        common.upload(r['id'],table_report)
    for p in (evidence,ROOT/'validation/WINDOWS-OFFLINE-SMOKE-REPORT.json',api_report):common.upload(r['id'],p)
    endpoint=f'/repos/{common.REPO}/contents/release-assets/windows-update.json'
    try:old=common.api(endpoint+'?ref=main')
    except Exception:old=None
    payload={'message':'Publish Windows application update '+VERSION,'content':base64.b64encode((json.dumps(manifest,indent=2)+'\n').encode()).decode(),'branch':'main'}
    if old:payload['sha']=old['sha']
    common.api(endpoint,payload,method='PUT')
    common.api(f"/repos/{common.REPO}/releases/{r['id']}",{'prerelease':False,'make_latest':'false'},method='PATCH')
    print('Windows application update channel published; model downloads unchanged',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','candidate','promote']);a=p.parse_args();{'build':build,'candidate':candidate,'promote':promote}[a.action]()
