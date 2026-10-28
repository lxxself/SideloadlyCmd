#!/usr/bin/env python3
"""Install a managed, stdlib-only runtime without copying signing state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile


START='# >>> sideloadlycmd PATH >>>'
END='# <<< sideloadlycmd PATH <<<'


def plan_path(home,bindir,shell):
    if shell not in {'zsh','bash'}: raise ValueError('PATH setup supports zsh or bash; select --shell explicitly')
    names=['.zprofile','.zshrc'] if shell=='zsh' else ['.bash_profile','.bashrc']
    quoted=shlex.quote(str(bindir))
    block=START+'\ncase ":$PATH:" in\n  *:'+quoted+':*) ;;\n  *) export PATH='+quoted+':"$PATH" ;;\nesac\n'+END
    edits=[]
    for name in names:
        path=home/name
        if path.is_symlink() or (path.exists() and path.stat().st_uid!=os.getuid()):
            raise ValueError('refusing to modify a symlink or foreign shell profile; configure PATH manually')
        old=path.read_text() if path.exists() else None
        text=old or ''
        if START in text or END in text:
            if text.count(START)!=1 or text.count(END)!=1 or text.index(START)>text.index(END):
                raise ValueError('incomplete or duplicate managed PATH block; inspect shell profile manually')
            first,last=text.index(START),text.index(END)+len(END)
            new=text[:first]+block+text[last:]
        else: new=text+('' if not text or text.endswith('\n') else '\n')+'\n'+block+'\n'
        edits.append((path,old,new,path.stat().st_mode&0o777 if path.exists() else 0o600))
    return edits


def write_profile(path,text,mode):
    fd,temporary=tempfile.mkstemp(prefix='.sideloadly-path-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream: stream.write(text)
        os.chmod(temporary,mode)
        os.replace(temporary,path)
    finally: Path(temporary).unlink(missing_ok=True)


def apply_path(edits):
    changed=[]
    try:
        for path,old,new,mode in edits:
            current=path.read_text() if path.exists() else None
            if path.is_symlink() or current!=old: raise OSError('shell profile changed during installation')
            if new!=old:
                write_profile(path,new,mode);changed.append((path,old,new,mode))
    except BaseException:
        for path,old,new,mode in reversed(changed):
            if not path.is_symlink() and path.read_text()==new:
                if old is None: path.unlink()
                else: write_profile(path,old,mode)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path.home()/'Library/Application Support/SideloadlyCmd/runtime')
    parser.add_argument('--bin-dir', type=Path, default=Path.home()/'.local/bin')
    parser.add_argument('--add-to-path',action='store_true',help='add an idempotent user PATH block for zsh/bash; never sources profiles automatically')
    parser.add_argument('--shell',choices=['zsh','bash'],help='shell for --add-to-path; defaults to SHELL')
    args = parser.parse_args()
    if sys.version_info < (3, 10): parser.error('Python 3.10 or newer is required')
    source = Path(__file__).resolve().parent.parent
    manifest_path = source/'MANIFEST.json'
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('format') != 1 or not isinstance(manifest.get('sha256'), dict):
                raise ValueError('unsupported manifest')
            for relative, digest in manifest['sha256'].items():
                member = Path(relative)
                if member.is_absolute() or '..' in member.parts:
                    raise ValueError('unsafe manifest path')
                file = source/member
                if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                    raise ValueError('package integrity mismatch: '+relative)
        except (OSError, ValueError, TypeError) as error:
            parser.error(str(error))
    prefix = args.prefix.expanduser().absolute()
    bindir = args.bin_dir.expanduser().absolute()
    edits=[]
    if args.add_to_path:
        try: edits=plan_path(Path.home(),bindir,args.shell or Path(os.environ.get('SHELL','/bin/zsh')).name)
        except (ValueError,OSError) as error: parser.error(str(error))
    marker = '.sideloadlycmd-runtime.json'
    launcher = bindir/'sideloadlycmd'
    if prefix.is_symlink() or (prefix.exists() and not (prefix/marker).is_file()):
        parser.error('destination is not a managed runtime; choose an empty --prefix')
    if launcher.is_symlink() or (launcher.exists() and '# sideloadlycmd managed launcher' not in launcher.read_text()):
        parser.error('refusing to overwrite an unrelated launcher')
    # Upgrading a running scheduler requires an explicit disable / enable cycle.
    prefix.parent.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    backup = prefix.with_name(prefix.name+'.previous')
    if backup.exists(): parser.error('previous runtime backup exists; inspect it before retrying')
    staging = Path(tempfile.mkdtemp(prefix='.sideloadlycmd-install-', dir=prefix.parent))
    moved = False
    installed = False
    tempname = None
    try:
        shutil.copytree(source/'src', staging/'src', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (staging/'scripts').mkdir()
        shutil.copy2(source/'scripts/sideloadlycmd', staging/'scripts/sideloadlycmd')
        (staging/marker).write_text(json.dumps({'format':1,'python':sys.executable})+'\n')
        body = '#!/bin/sh\n# sideloadlycmd managed launcher\nexec '+shlex.quote(sys.executable)+' -I '+shlex.quote(str(prefix/'scripts/sideloadlycmd'))+' "$@"\n'
        fd, tempname = tempfile.mkstemp(prefix='.sideloadlycmd-',dir=bindir)
        with os.fdopen(fd,'w') as stream: stream.write(body)
        os.chmod(tempname,0o755)
        if prefix.exists(): prefix.rename(backup); moved=True
        staging.rename(prefix)
        installed = True
        os.replace(tempname,launcher)
        tempname = None
        if moved: shutil.rmtree(backup)
    except BaseException:
        if tempname is not None:
            if installed and prefix.exists(): shutil.rmtree(prefix)
            if moved and backup.exists(): backup.rename(prefix)
        raise
    finally:
        if tempname is not None: Path(tempname).unlink(missing_ok=True)
        if staging.exists(): shutil.rmtree(staging)
    try: apply_path(edits)
    except OSError:
        print(json.dumps({'state':'runtime_installed_path_setup_failed','launcher':str(launcher),'message':'runtime installed; shell profile update rolled back, configure PATH manually'}))
        return 10
    print(json.dumps({'state':'runtime_installed','launcher':str(launcher),'scheduler':'not_modified','signing_state':'not_copied','path_configured':bool(edits),'path_profiles':[str(row[0]) for row in edits],'open_new_terminal':bool(edits)}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
