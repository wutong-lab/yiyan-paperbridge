"""Publish independent Mac application updates and immutable model archives."""
import argparse
import base64
import re
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import publish_offline_ci as common
from build_macos_installer import build as build_installer
CONFIG=json.loads((ROOT/'release-assets/macos-version.json').read_text())
VERSION=os.environ.get('PAPERBRIDGE_RELEASE_VERSION',CONFIG['version'])
assert re.fullmatch(r'\d+\.\d+\.\d+',VERSION)
TAG=f'v{VERSION}-macos';MODEL_TAG=CONFIG['model_tag']
APP=ROOT/'dist/release/译研.app'
BASE=f'https://github.com/{common.DOWNLOAD_REPO}/releases/download/'


def find_release(tag):
    return next((r for r in common.api(f'/repos/{common.REPO}/releases?per_page=100') if r.get('tag_name')==tag or (r.get('draft') and ('<!-- paperbridge-tag: '+tag+' -->' in (r.get('body') or '') or '/releases/download/'+tag+'/' in (r.get('body') or '')))),None)


def release(tag,title):
    existing=find_release(tag)
    if existing:
        if not existing['draft']: raise RuntimeError('Published releases are immutable: '+tag)
        return existing
    return common.api(f'/repos/{common.REPO}/releases',{'tag_name':tag,'target_commitish':os.environ['GITHUB_SHA'],'name':title,'body':'<!-- paperbridge-tag: '+tag+' -->','draft':True,'prerelease':True})


def model_assets():
    common.require_runner()
    existing=find_release(MODEL_TAG)
    if existing and not existing['draft']:
        asset=next(a for a in existing['assets'] if a['name']=='macos-models.json')
        common.download(asset['browser_download_url'],ROOT/'.cache/macos-models.json',asset['digest'].removeprefix('sha256:'),asset['size'])
        catalog=json.loads((ROOT/'.cache/macos-models.json').read_text())
        for profile,name in [('standard','qwen3-4b'),('precision','qwen3-14b')]:
            expected=json.loads((ROOT/'release-assets/model-snapshots'/f'{name}.json').read_text())
            assert catalog['editions'][profile]['snapshot']==expected
        print('Pinned model release already published; app updates reuse it',flush=True)
        return
    target=release(MODEL_TAG,'译研 · Mac 独立模型资源 · 4B / 14B')
    catalog={'schema':1,'editions':{}}
    for profile,name in [('standard','qwen3-4b'),('precision','qwen3-14b')]:
        snapshot=json.loads((ROOT/'release-assets/model-snapshots'/f'{name}.json').read_text())
        model=ROOT/'.cache/macos-model-source'/name;model.mkdir(parents=True,exist_ok=True)
        for item in snapshot['files']:
            assert Path(item['file']).name==item['file']
            common.download(f"https://huggingface.co/{snapshot['repo']}/resolve/{snapshot['revision']}/{item['file']}?download=true",model/item['file'],item['sha256'],item['size'])
        (model/'installation.json').write_text(json.dumps(snapshot,indent=2)+'\n')
        writer=common.PartWriter(ROOT/'.cache/macos-model-parts',name+'.tar',lambda p:common.upload(target['id'],p))
        with tarfile.open(fileobj=writer,mode='w|',dereference=False) as archive: archive.add(model,arcname=name)
        writer.finish_part()
        parts=[{'name':p['file'],'bytes':p['bytes'],'sha256':p['sha256'],'url':BASE+MODEL_TAG+'/'+p['file']} for p in writer.parts]
        catalog['editions'][profile]={'directory':name,'snapshot':snapshot,'parts':parts}
        shutil.rmtree(model)
    path=ROOT/'.cache/macos-models.json';path.write_text(json.dumps(catalog,indent=2)+'\n');common.upload(target['id'],path)
    common.api(f"/repos/{common.REPO}/releases/{target['id']}",{'draft':False,'prerelease':False,'make_latest':'false','body':'Mac 安装器自动获取的独立模型资源。用户从软件首页下载“一键安装器”，选择 4B 或 14B 即可。模型固定到已校验的 Qwen 官方版本，应用更新沿用本地模型。'},method='PATCH')


def build_app():
    common.require_runner()
    import package_macos as package
    original_smoke=package.smoke_test
    def portable_cpu_smoke(app):
        # Import and PDF runtime checks on VM; real MLX inference is checked on a physical Mac.
        python=app/'Contents/Resources/Python/bin/python3.12'
        subprocess.run([str(python),'-X','utf8','-c','import fastapi,uvicorn,pymupdf,psutil,onnxruntime,babeldoc,fontTools; print("Bundled PDF/UI imports passed")'],check=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},cwd='/tmp')
    package.VERSION=VERSION;package.smoke_test=portable_cpu_smoke
    package.SCRIPTS.extend(['publish_offline_ci.py','publish_macos_oneclick.py','build_macos_installer.py','test_macos_installer.py','smoke_reader_installed.py','smoke_table_translation.py'])
    sys.argv=['package_macos.py','--no-dmg','--python-runtime',str(ROOT/'.cache/python')]
    package.main()
    resources=APP/'Contents/Resources';layout=resources/'LayoutAssets'
    shutil.copytree(ROOT/'.cache/babeldoc',layout,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (resources/'Project.plist').write_bytes(plistlib.dumps({'Portable':True,'SharedDataDirectory':'PaperBridge-Offline','ManagedModels':True}))
    build_installer(resources/'Updater.app')
    source=ROOT/'dist/release/PaperBridge-Source'
    for name in ('macos-version.json','macos-update.json'):
        shutil.copy2(ROOT/'release-assets'/name,source/'release-assets'/name)
    shutil.copytree(ROOT/'release-assets/model-snapshots',source/'release-assets/model-snapshots')
    shutil.copytree(ROOT/'release-assets/macos-bootstrap',source/'release-assets/macos-bootstrap')
    package.archive_tree(source,ROOT/'dist/release'/f'PaperBridge-{VERSION}-Source.zip',f'PaperBridge-{VERSION}')
    subprocess.run([sys.executable,str(ROOT/'scripts/verify_publication.py'),'--archive',str(ROOT/'dist/release'/f'PaperBridge-{VERSION}-Source.zip')],check=True)
    shutil.copy2(ROOT/'dist/release'/f'PaperBridge-{VERSION}-Source.zip',resources/'ApplicationSource.zip')
    # No weights are stored inside the app. Runtime, layout assets and licenses remain bundled.
    assert not (resources/'Models').exists()
    subprocess.run(['codesign','--force','--deep','--sign','-',str(APP)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True)
    out=ROOT/'dist/macos-oneclick';out.mkdir(parents=True,exist_ok=True)
    archive=out/f'Yiyan-{VERSION}-AppleSilicon-App.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=3,dereference=False) as a:a.add(APP,arcname='译研.app')
    subprocess.run([sys.executable,str(ROOT/'scripts/test_macos_installer.py'),'--archive',str(archive)],check=True)
    (out/'MACOS-ARCHIVE-VALIDATION.json').write_text(json.dumps({'version':VERSION,'inherited_locale':'C','actual_application_archive_extracted':True,'extracted_app_signature_verified':True,'installer_checks':20},indent=2)+'\n')
    manifest={'schema':1,'version':VERSION,'minimum_macos':CONFIG['minimum_macos'],'architecture':'arm64','app':{'name':archive.name,'bytes':archive.stat().st_size,'sha256':common.sha(archive),'url':BASE+TAG+'/'+archive.name},'models_url':BASE+MODEL_TAG+'/macos-models.json'}
    (out/'macos-app.json').write_text(json.dumps(manifest,indent=2)+'\n')
    helper=out/'Installer/安装译研.app';build_installer(helper,release_manifest=out/'macos-app.json')
    subprocess.run(['hdiutil','create','-volname','译研一键安装','-srcfolder',str(helper.parent),'-format','UDZO','-ov',str(out/'Yiyan-Mac-OneClick-Setup.dmg')],check=True)
    # Capture both real model-selection states and verify first startup of the model-free app.
    subprocess.run([str(helper/'Contents/MacOS/YiyanSetup'),'--preview-smoke',str(out/'screenshots')],check=True,timeout=90)
    env={**os.environ,'PAPERBRIDGE_DATA_HOME':str(ROOT/'.cache/macos-first-launch')}
    subprocess.run([str(APP/'Contents/MacOS/PaperBridge'),'--smoke-test'],env=env,check=True,timeout=180)
    smoke=ROOT/'.cache/macos-first-launch/.cache/desktop'
    report=json.loads((smoke/'macos-ui-smoke.json').read_text());assert report['loaded'] and report['status']['layout_installed']
    shutil.copy2(smoke/'macos-ui-smoke.json',out/'MACOS-FIRST-LAUNCH.json');shutil.copy2(smoke/'macos-ui-smoke.png',out/'screenshots/macos-workspace.png')
    report={'version':VERSION,'application_has_models':False,'layout_bundled':True,'runtime_bundled':True,'models_outside_application':True,'first_launch_passed':True,'source_commit':os.environ['GITHUB_SHA']}
    (out/'MACOS-BUILD-REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
    target=release(TAG,'译研 · Mac 一键安装与独立更新 · 4B / 14B 自选')
    for p in [*out.glob('*.*'),*out.glob('screenshots/*.png'),ROOT/'dist/release'/f'PaperBridge-{VERSION}-Source.zip',ROOT/'dist/release'/f'PaperBridge-{VERSION}-ThirdPartySource.zip']:
        common.upload(target['id'],p)
    notes=ROOT/'docs'/f'RELEASE_v{VERSION}_MACOS.md'
    if not notes.is_file():notes=ROOT/'docs/RELEASE_v1.8.0_MACOS.md'
    common.api(f"/repos/{common.REPO}/releases/{target['id']}",{'body':notes.read_text().replace('/v1.8.0-macos/','/'+TAG+'/')},method='PATCH')


def publish_app():
    common.require_runner();target=find_release(TAG);assert target and target['draft']
    models=find_release(MODEL_TAG);assert models and not models['draft']
    names={a['name'] for a in target['assets']}
    assert {'macos-app.json','Yiyan-Mac-OneClick-Setup.dmg','MACOS-FIRST-LAUNCH.json','MACOS-BUILD-REPORT.json'} <= names
    common.api(f"/repos/{common.REPO}/releases/{target['id']}",{'tag_name':TAG,'draft':False,'prerelease':True,'make_latest':'false'},method='PATCH')
    print('Published Mac one-click candidate with independent models and app update manifest',flush=True)


def promote_app():
    common.require_runner()
    target=find_release(TAG)
    assert target and not target['draft']
    validation_path=ROOT/'release-assets'/f'verification-{VERSION}-macos.json'
    if CONFIG.get('require_table_translation_validation') and not validation_path.is_file():
        print('Mac candidate is ready. Stable update waits for physical Mac translation validation.',flush=True)
        return
    records={a['name']:a for a in target['assets']}
    for filename in ('macos-app.json','MACOS-BUILD-REPORT.json','MACOS-FIRST-LAUNCH.json'):
        asset=records[filename]
        path=ROOT/'.cache'/filename
        common.download(asset['browser_download_url'],path,asset['digest'].removeprefix('sha256:'),asset['size'])
    manifest=json.loads((ROOT/'.cache/macos-app.json').read_text())
    build=json.loads((ROOT/'.cache/MACOS-BUILD-REPORT.json').read_text())
    launch=json.loads((ROOT/'.cache/MACOS-FIRST-LAUNCH.json').read_text())
    assert manifest['schema']==1 and manifest['version']==VERSION
    if CONFIG.get('require_table_translation_validation'):
        validation=json.loads(validation_path.read_text())
        assert validation['version']==VERSION and validation['source_commit']==build['source_commit']
        assert validation['passed'] and validation['actual_installed_runtime'] and validation['real_4b_inference']
        assert validation['native_installer_passed'] and not validation['development_source_override']
        assert validation['native_version']==VERSION and not validation['user_credential_used']
        assert {case['case'] for case in validation['cases']}=={'off','on'}
        assert all(case['passed'] and case['status']=='complete' for case in validation['cases'])
        if CONFIG.get('require_reader_validation'):
            assert validation['reader_validation']['passed']
            assert validation['reader_validation']['version']==VERSION
            assert validation['reader_validation']['installed_engine_verified']
        common.upload(target['id'],validation_path)
    assert build['first_launch_passed'] and build['models_outside_application'] and not build['application_has_models']
    assert launch['loaded'] and launch['status']['layout_installed']
    evidence=records['MACOS-ARCHIVE-VALIDATION.json']
    common.download(evidence['browser_download_url'],ROOT/'.cache/MACOS-ARCHIVE-VALIDATION.json',evidence['digest'].removeprefix('sha256:'),evidence['size'])
    archive_check=json.loads((ROOT/'.cache/MACOS-ARCHIVE-VALIDATION.json').read_text())
    assert archive_check['version']==VERSION and archive_check['actual_application_archive_extracted'] and archive_check['extracted_app_signature_verified'] and archive_check['inherited_locale']=='C'
    archive=records[manifest['app']['name']]
    assert archive['digest']=='sha256:'+manifest['app']['sha256'] and archive['size']==manifest['app']['bytes']
    channel={'schema':1,'manifest_url':BASE+TAG+'/macos-app.json'}
    # Small app metadata is also available through the repository's public CDN.
    # Anonymous acceleration gateways carry only SHA-verified binary assets.
    endpoint=f'/repos/{common.REPO}/contents/release-assets/macos-app.json'
    try: old_manifest=common.api(endpoint+'?ref=main')
    except Exception: old_manifest=None
    if old_manifest is None or json.loads(base64.b64decode(old_manifest['content']))!=manifest:
        update={'message':'Publish direct Mac manifest '+VERSION,'content':base64.b64encode((json.dumps(manifest,indent=2)+'\n').encode()).decode(),'branch':'main'}
        if old_manifest: update['sha']=old_manifest['sha']
        common.api(endpoint,update,method='PUT')
    old=common.api(f'/repos/{common.REPO}/contents/release-assets/macos-update.json?ref=main')
    if json.loads(base64.b64decode(old['content'])) != channel:
        common.api(f'/repos/{common.REPO}/contents/release-assets/macos-update.json',{'message':'Publish Mac application update '+VERSION,'content':base64.b64encode((json.dumps(channel,indent=2)+'\n').encode()).decode(),'sha':old['sha'],'branch':'main'},method='PUT')
    common.api(f"/repos/{common.REPO}/releases/{target['id']}",{'prerelease':False,'make_latest':'false'},method='PATCH')
    print('Published stable Mac '+VERSION+' and its app-only update channel; model release unchanged',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['models','build','publish','promote']);a=p.parse_args()
    {'models':model_assets,'build':build_app,'publish':publish_app,'promote':promote_app}[a.action]()
