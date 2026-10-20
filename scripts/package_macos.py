"""Build a relocatable Apple Silicon app, DMG, public source and notices.

This script has an explicit source allowlist. User papers, models, histories,
logs, caches and the existing virtualenv are never included in source archives.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.4.0"
RELEASE = ROOT / "dist/release"
APP = RELEASE / "译研.app"
ASSETS = ROOT / "release-assets"
SCRIPTS = ['build_macos.py', 'package_macos.py', 'prepare_release_licenses.py',
           'verify_publication.py', 'download_model.py', 'install_layout.py', 'make_sample.py', 'bundle_models.py', 'split_release.py']


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def ignore(folder, names):
    return [name for name in names if name in {'__pycache__', 'bin', 'obj'} or name.endswith('.pyc') or name == '.DS_Store']


def archive_tree(root, destination, prefix):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file() and not path.is_symlink():
                archive.write(path, str(Path(prefix) / path.relative_to(root)))


def source_tree(destination, offline=False):
    destination.mkdir(parents=True, exist_ok=True)
    for name in ['paperbridge', 'desktop', 'tests']:
        shutil.copytree(ROOT / name, destination / name, ignore=ignore)
    shutil.copytree(ROOT / 'docs', destination / 'docs', ignore=ignore)
    (destination / 'scripts').mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / 'scripts' / name, destination / 'scripts' / name)
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements.txt', 'requirements.lock.txt', 'run.command', 'pytest.ini', '.gitignore']:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(ASSETS / ('README-full.md' if offline else 'README-public.md'), destination / 'README.md')
    shutil.copytree(ASSETS / 'licenses', destination / 'release-assets/licenses', ignore=ignore)
    for name in ['README-public.md', 'README-full.md', 'DEPENDENCIES.json']:
        shutil.copy2(ASSETS / name, destination / 'release-assets' / name)
    # Reject accidental inclusion of developer paths, keys or manuscript files.
    for path in destination.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix.lower() in {'.pdf', '.safetensors', '.log'} or path.name.startswith('.env'):
            raise RuntimeError(f'Private artifact in public source: {path.relative_to(destination)}')
        if (str(Path.home()) + '/').encode() in path.read_bytes():
            raise RuntimeError(f'Personal path in public source: {path.relative_to(destination)}')


def runtime_tree(base, destination):
    # Base must be a standalone relocatable Python, never a Homebrew symlink.
    if not (base / 'bin/python3.12').is_file() or not (base / 'lib/python3.12/encodings').is_dir():
        raise RuntimeError('Provide a standalone Python 3.12 runtime directory')
    (destination / 'bin').mkdir(parents=True)
    shutil.copy2((base / 'bin/python3.12').resolve(), destination / 'bin/python3.12')
    for name in ['python', 'python3']:
        (destination / 'bin' / name).symlink_to('python3.12')
    # Exclude unrelated global packages from the original standalone runtime.
    shutil.copytree(base / 'lib', destination / 'lib', symlinks=True,
                    ignore=lambda folder, names: ignore(folder, names) +
                    (['site-packages'] if Path(folder).name == 'python3.12' else []) +
                    (['pkgconfig'] if Path(folder) == base / 'lib' else []))
    packages = destination / 'lib/python3.12/site-packages'
    shutil.copytree(ROOT / '.venv/lib/python3.12/site-packages', packages, ignore=ignore)
    for path in packages.rglob('direct_url.json'):
        path.unlink()
    for path in destination.rglob('*'):
        if path.is_symlink() and os.path.isabs(os.readlink(path)):
            raise RuntimeError(f'Nonportable absolute symlink: {path}')


def smoke_test(app):
    with tempfile.TemporaryDirectory(prefix='paperbridge-relocation-') as temporary:
        moved = Path(temporary) / 'Different Folder' / '译研.app'
        moved.parent.mkdir()
        shutil.copytree(app, moved, symlinks=True)
        resources = moved / 'Contents/Resources'
        python = resources / 'Python/bin/python3.12'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
        code = '''import sys, pathlib, ssl, bz2, lzma, sqlite3, ctypes
import fastapi, uvicorn, httpx, pymupdf, psutil, onnxruntime, babeldoc, fontTools
onnxruntime.disable_telemetry_events()
import mlx.core as mx
assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve(), sys.prefix
assert all(not p or pathlib.Path(p).resolve().is_relative_to(pathlib.Path(sys.argv[2]).resolve()) for p in sys.path), sys.path
assert mx.array([1, 2]).tolist() == [1, 2]
print('Relocated runtime imports and Metal computation passed')
'''
        run(python, '-c', code, resources / 'Python', temporary, env=env, cwd=temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-runtime', type=Path, default=Path(sys.base_prefix))
    parser.add_argument('--no-dmg', action='store_true')
    parser.add_argument('--include-models', action='store_true', help='Bundle installed 14B/4B and offline layout assets')
    args = parser.parse_args()
    global RELEASE, APP
    if args.include_models:
        RELEASE = ROOT / 'dist/release-full'
        APP = RELEASE / '译研.app'
    if not (ASSETS / 'DEPENDENCIES.json').is_file() or not (ASSETS / 'vendor-sources/SOURCES.json').is_file():
        raise RuntimeError('Run prepare_release_licenses.py before packaging')
    RELEASE.mkdir(parents=True, exist_ok=True)
    # Remove only this script's previous app output; leave all other files alone.
    if APP.exists():
        shutil.rmtree(APP)
    build_args = ['--portable', '--output', APP, '--version', VERSION] + (['--offline-models'] if args.include_models else [])
    run(sys.executable, ROOT / 'scripts/build_macos.py', *build_args, cwd=ROOT)
    resources = APP / 'Contents/Resources'
    source = RELEASE / 'PaperBridge-Source'
    if source.exists():
        shutil.rmtree(source)
    source_tree(source, offline=args.include_models)
    engine = resources / 'Engine'
    engine.mkdir()
    for name in ['paperbridge', 'scripts']:
        shutil.copytree(source / name, engine / name)
    for name in ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy2(source / name, engine / name)
    index = hashlib.sha256()
    for path in sorted(engine.rglob('*')):
        if path.is_file():
            index.update(str(path.relative_to(engine)).encode()); index.update(path.read_bytes())
    (engine / 'BUILD_ID').write_text(index.hexdigest())
    runtime = resources / 'Python'
    runtime_tree(args.python_runtime.resolve(), runtime)
    run(runtime / 'bin/python', engine / 'scripts/make_sample.py', engine / 'examples/sci-layout-sample.pdf',
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    # Readable notices, exact application source, and separate upstream source.
    shutil.copytree(ASSETS / 'licenses', resources / 'Licenses', ignore=ignore)
    shutil.copy2(ASSETS / 'DEPENDENCIES.json', resources / 'DEPENDENCIES.json')
    app_source = RELEASE / f'PaperBridge-{VERSION}-Source.zip'
    archive_tree(source, app_source, f'PaperBridge-{VERSION}')
    shutil.copy2(app_source, resources / 'ApplicationSource.zip')
    upstream = RELEASE / f'PaperBridge-{VERSION}-ThirdPartySource.zip'
    archive_tree(ASSETS / 'vendor-sources', upstream, 'vendor-sources')
    run('codesign', '--force', '--deep', '--sign', '-', APP)
    run('codesign', '--verify', '--deep', '--strict', APP)
    smoke_test(APP)
    if args.include_models:
        sys.path.insert(0, str(ROOT))
        from scripts.bundle_models import bundle_models
        bundle_models(ROOT, resources)
    # Resource addition invalidates the previous signature. Sign again after
    # adding the unchanged model and layout assets in the full edition.
    if args.include_models:
        run('codesign', '--force', '--deep', '--sign', '-', APP)
        run('codesign', '--verify', '--deep', '--strict', APP)
    instructions = ASSETS / ('README-full.md' if args.include_models else 'README-public.md')
    shutil.copy2(instructions, RELEASE / '安装与使用说明.md')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', RELEASE / 'THIRD_PARTY_NOTICES.md')
    artifacts = [app_source, upstream]
    if not args.no_dmg:
        staging = ROOT / '.cache' / ('dmg-staging-full' if args.include_models else 'dmg-staging')
        if staging.exists(): shutil.rmtree(staging)
        staging.mkdir(parents=True)
        # APFS clone if possible, falling back to a regular copy.
        run('ditto', '--noextattr', '--noqtn', APP, staging / '译研.app')
        (staging / 'Applications').symlink_to('/Applications')
        for item in [app_source, upstream, RELEASE / '安装与使用说明.md', RELEASE / 'THIRD_PARTY_NOTICES.md']:
            shutil.copy2(item, staging / item.name)
        suffix = '-Offline-14B' if args.include_models else ''
        dmg = RELEASE / f'Yiyan-{VERSION}-AppleSilicon{suffix}.dmg'
        run('hdiutil', 'create', '-volname', f'译研 {VERSION}', '-srcfolder', staging,
            '-format', 'UDZO', '-imagekey', 'zlib-level=6', '-ov', dmg)
        shutil.rmtree(staging)
        artifacts.append(dmg)
    sums = '\n'.join(f'{hashlib.file_digest(path.open("rb"), "sha256").hexdigest()}  {path.name}' for path in artifacts) + '\n'
    (RELEASE / 'SHA256SUMS.txt').write_text(sums)
    report = {'version': VERSION, 'architecture': 'arm64', 'minimum_macos': '26.2',
              'models_included': args.include_models, 'user_documents_included': False, 'developer_id_signed': False,
              'notarized': False, 'relocation_smoke_test': 'passed',
              'artifacts': [{'name': p.name, 'bytes': p.stat().st_size} for p in artifacts]}
    (RELEASE / 'PACKAGE_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if args.include_models and not args.no_dmg:
        from scripts.split_release import split_release
        split_release(dmg, RELEASE / 'GitHub-上传文件')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
