"""Build the native model-selecting Mac installer and its small downloadable DMG."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def build(output, dmg=None, release_manifest=None):
    output=Path(output)
    resources=output/'Contents/Resources';binary=output/'Contents/MacOS'
    resources.mkdir(parents=True,exist_ok=True);binary.mkdir(parents=True,exist_ok=True)
    cache=ROOT/'.cache/macos-setup-build';cache.mkdir(parents=True,exist_ok=True)
    subprocess.run(['xcrun','swiftc','-swift-version','5','-O','-target','arm64-apple-macosx12.0','-module-cache-path',str(cache/'modules'),'-framework','AppKit','-framework','CryptoKit',*[str(ROOT/'desktop/macos-setup'/n) for n in ('DownloadNetwork.swift','InstallerCore.swift','InstallerWindow.swift','main.swift')],'-o',str(binary/'YiyanSetup')],check=True)
    shutil.copy2(ROOT/'release-assets/macos-bootstrap/macos-models.json',resources/'BootstrapModels.json')
    if release_manifest:
        shutil.copy2(release_manifest,resources/'BootstrapRelease.json')
    else:
        (resources/'BootstrapRelease.json').unlink(missing_ok=True)
    icon=ROOT/'desktop/macos/AppIcon.icns'
    if not icon.is_file(): icon=ROOT/'dist/译研.app/Contents/Resources/AppIcon.icns'
    shutil.copy2(icon,resources/'AppIcon.icns')
    version=json.loads((ROOT/'release-assets/macos-version.json').read_text())['version']
    (output/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleName':'安装译研','CFBundleDisplayName':'安装译研','CFBundleIdentifier':'org.paperbridge.yiyan.installer','CFBundleExecutable':'YiyanSetup','CFBundlePackageType':'APPL','CFBundleShortVersionString':version,'CFBundleVersion':version.replace('.',''),'CFBundleIconFile':'AppIcon','LSMinimumSystemVersion':'12.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication'}))
    subprocess.run(['codesign','--force','--deep','--sign','-',str(output)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(output)],check=True)
    if dmg:
        subprocess.run(['hdiutil','create','-volname','译研一键安装','-srcfolder',str(output.parent),'-format','UDZO','-ov',str(dmg)],check=True)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'dist/macos-installer/安装译研.app');p.add_argument('--dmg',type=Path);a=p.parse_args();build(a.output,a.dmg)
