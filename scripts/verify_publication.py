"""Check the exact public source set without reading any local credential store."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile

FORBIDDEN = {'.env', 'api-profiles.json', 'PRIVATE-PREVIEW.json'}
PRIVATE_DIRS = {'settings', '.api-secrets', 'data', 'output', '验证记录', '论文PDF'}
TOKENS = re.compile(rb'(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def check(name, content):
    path = Path(name)
    if (path.name in FORBIDDEN or path.name.startswith('.env') or
        (PRIVATE_DIRS.intersection(path.parts) and path.name != '.gitkeep') or
        path.suffix.lower() in {'.safetensors', '.gguf', '.log', '.key', '.pem'}):
        raise RuntimeError('Private artifact in publish set: ' + name)
    if path.suffix.lower() == '.pdf' and name != 'examples/sci-layout-sample.pdf':
        raise RuntimeError('Unexpected PDF in public source: ' + name)
    for token in TOKENS.findall(content):
        # Explicitly synthetic regression-test credentials are public fixtures.
        if path.parts[0] == 'tests' and any(marker in token.lower() for marker in (b'test', b'dummy', b'synthetic')):
            continue
        raise RuntimeError('Credential-shaped value in publish set: ' + name)
    if not name.startswith('release-assets/licenses/') and re.search(rb'/' + rb'Users/[A-Za-z0-9_.-]+/', content):
        raise RuntimeError('Developer personal path in publish set: ' + name)


def verify(root):
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, check=True, stdout=subprocess.PIPE)
    names = sorted(set(x.decode('utf-8') for x in result.stdout.split(b'\0') if x))
    checked = 0
    for name in names:
        path = root / name
        if path.is_file():
            check(name, path.read_bytes())
            checked += 1
    return {'passed': True, 'files_checked': checked, 'local_credential_store_read': False,
            'scope': 'tracked source and explicitly non-ignored new source files'}


def verify_zip(archive):
    checked = 0
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if entry.is_dir():
                continue
            parts = Path(entry.filename).parts
            # Source archives have a single versioned enclosing directory.
            relative = '/'.join(parts[1:])
            check(relative, bundle.read(entry))
            checked += 1
    return {'passed': True, 'source_archive': Path(archive).name, 'files_checked': checked}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    result = verify_zip(args.archive) if args.archive else verify(Path(__file__).resolve().parents[1])
    print(json.dumps(result, indent=2))
