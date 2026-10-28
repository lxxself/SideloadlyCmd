#!/usr/bin/env python3
"""Check local documentation links and public archive contents; no device use."""
import argparse
from email.parser import BytesParser
from pathlib import Path,PurePosixPath
import re
import tarfile
from urllib.parse import unquote,urlsplit
import zipfile

ROOT=Path(__file__).resolve().parent.parent
FORBIDDEN={'.local-dev','analysis','research','.git','__pycache__'}
PRIVATE_SUFFIXES={'.ipa','.mobileprovision','.p12','.p8','.pfx','.pem','.keychain','.keychain-db','.pyc'}


def check_links():
    files=[*ROOT.glob('*.md'),*(ROOT/'docs').rglob('*.md'),*(ROOT/'.agents/skills').rglob('*.md')]
    errors=[]
    for file in files:
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',file.read_text()):
            target=target.strip('<>')
            url=urlsplit(target)
            if url.scheme or not url.path: continue
            path=(file.parent/unquote(url.path)).resolve()
            if not path.is_relative_to(ROOT) or not path.exists(): errors.append(f'{file.relative_to(ROOT)}: broken/local-external link {target}')
    if errors: raise ValueError('\n'.join(errors))
    print(f'Documentation links: {len(files)} files passed')


def check_archive(path):
    wheel=path.suffix=='.whl'
    if wheel:
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            metadata=next(name for name in names if name.endswith('.dist-info/METADATA'))
            if BytesParser().parsebytes(archive.read(metadata)).get('License-Expression')!='MIT':
                raise ValueError('wheel license expression is not MIT')
    else:
        with tarfile.open(path) as archive: names=archive.getnames()
    for name in names:
        member=PurePosixPath(name)
        if member.is_absolute() or '..' in member.parts or any(part in FORBIDDEN for part in member.parts) or member.suffix in PRIVATE_SUFFIXES or member.name.startswith('.env'):
            raise ValueError(f'{path.name}: forbidden archive member {name}')
    required=['sideloadlycmd/cli.py','sideloadlycmd/_entry.py','sideloadlycmd/_installation_query.py','sideloadlycmd/background_run.py','sideloadlycmd/sideloadly.applescript','LICENSE']
    if not wheel: required+=['README.md','docs/usage.md','scripts/install_macos.py','scripts/check_release.py','scripts/generate_cli_reference.py','tests/test_cli.py','.agents/skills/sideloadly-cli/SKILL.md']
    for suffix in required:
        if not any(name==suffix or name.endswith('/'+suffix) for name in names):
            raise ValueError(f'{path.name}: required member missing: {suffix}')
    print(f'{path.name}: {len(names)} members; public boundaries and required files passed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs-only',action='store_true')
    parser.add_argument('archives',nargs='*',type=Path)
    args=parser.parse_args()
    try:
        check_links()
        if not args.docs_only:
            archives=args.archives or sorted((ROOT/'dist').glob('sideloadlycmd*.whl'))+sorted((ROOT/'dist').glob('sideloadlycmd*.tar.gz'))
            if not archives: raise ValueError('no release archives found; build them first')
            for path in archives: check_archive(path)
    except (ValueError,OSError,StopIteration,zipfile.BadZipFile,tarfile.TarError) as error:
        print(str(error),file=__import__('sys').stderr)
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
