#!/usr/bin/env python3
"""Build a portable source kit from an explicit public-file allowlist."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import shutil

root=Path(__file__).resolve().parent.parent
output=root/'dist/sideloadlycmd-macos.tar.gz'
files=[]
for directory,extensions in [('src',{'.py','.applescript'}),('scripts',{'.py'}),('tests',{'.py'}),('docs',{'.md'}),('.agents/skills/sideloadly-cli',{'.md','.yaml'})]:
    files.extend(path for path in (root/directory).rglob('*') if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts)
files += [root/name for name in ('scripts/sideloadlycmd','README.md','README.en.md','LICENSE','CONTRIBUTING.md','SECURITY.md','CHANGELOG.md','pyproject.toml','MANIFEST.in')]
with tempfile.TemporaryDirectory() as tmp:
    kit=Path(tmp)/'sideloadlycmd-macos'
    manifest={}
    for path in sorted(set(files)):
        if path.is_symlink(): raise ValueError('release inputs cannot be symbolic links: '+str(path))
        rel=path.relative_to(root)
        target=kit/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
        manifest[str(rel)]=hashlib.sha256(target.read_bytes()).hexdigest()
    (kit/'MANIFEST.json').write_text(json.dumps({'format':1,'sha256':manifest},indent=2)+'\n')
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output,'w:gz') as archive: archive.add(kit,arcname=kit.name)
print(output)
print('sha256 '+hashlib.sha256(output.read_bytes()).hexdigest())
