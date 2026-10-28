"""Preserve Xcode's non-secret account references around delegated CLI builds."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import plistlib
import re
import tempfile

from .errors import CliError, ExitCode
from .private_state import write_json

DOMAIN='com.apple.dt.Xcode'
KEY='DVTDeveloperAccountManagerAppleIDLists'


def _foundation():
    cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr=ctypes.c_void_p; size=ctypes.c_long
    declarations={
        'CFStringCreateWithCString':([ptr,ctypes.c_char_p,ctypes.c_uint32],ptr),
        'CFPreferencesCopyAppValue':([ptr,ptr],ptr),
        'CFPreferencesSetAppValue':([ptr,ptr,ptr],None),
        'CFPreferencesAppSynchronize':([ptr],ctypes.c_bool),
        'CFPropertyListCreateData':([ptr,ptr,size,ctypes.c_ulong,ptr],ptr),
        'CFPropertyListCreateWithData':([ptr,ptr,ctypes.c_ulong,ptr,ptr],ptr),
        'CFDataCreate':([ptr,ptr,size],ptr),
        'CFDataGetLength':([ptr],size),
        'CFDataGetBytePtr':([ptr],ptr),
        'CFRelease':([ptr],None),
    }
    for name,(args,result) in declarations.items():
        method=getattr(cf,name);method.argtypes=args;method.restype=result
    return cf


def _preference(value=None,*,write=False):
    cf=_foundation(); owned=[]
    def keep(pointer):
        if not pointer: raise OSError('Xcode preference conversion failed')
        owned.append(pointer);return pointer
    try:
        key=keep(cf.CFStringCreateWithCString(None,KEY.encode(),0x08000100))
        domain=keep(cf.CFStringCreateWithCString(None,DOMAIN.encode(),0x08000100))
        if write:
            raw=plistlib.dumps(value,fmt=plistlib.FMT_BINARY)
            buffer=ctypes.create_string_buffer(raw)
            data=keep(cf.CFDataCreate(None,buffer,len(raw)))
            obj=keep(cf.CFPropertyListCreateWithData(None,data,0,None,None))
            cf.CFPreferencesSetAppValue(key,obj,domain)
            if not cf.CFPreferencesAppSynchronize(domain):
                raise OSError('Xcode account reference restoration did not synchronize')
            return None
        cf.CFPreferencesAppSynchronize(domain)
        obj=cf.CFPreferencesCopyAppValue(key,domain)
        if not obj: return {}
        keep(obj)
        data=keep(cf.CFPropertyListCreateData(None,obj,200,0,None))
        raw=ctypes.string_at(cf.CFDataGetBytePtr(data),cf.CFDataGetLength(data))
        return plistlib.loads(raw)
    finally:
        for obj in reversed(owned): cf.CFRelease(obj)


def _identifiers(value):
    if not isinstance(value,dict): return set()
    return {entry.get('identifier') or entry.get('username') for items in value.values() if isinstance(items,list)
            for entry in items if isinstance(entry,dict) and isinstance(entry.get('identifier') or entry.get('username'),str)}


def _validate_metadata(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if re.search('password|token|secret|credential',str(key),re.I):
                raise CliError('Xcode account reference contains unexpected credential fields; no snapshot was written',
                               ExitCode.PREFLIGHT,'unexpected_account_metadata')
            _validate_metadata(item)
    elif isinstance(value,list):
        for item in value: _validate_metadata(item)
    elif not isinstance(value,(str,int,float,bool,type(None))):
        raise CliError('Xcode account metadata has an unsupported value type',ExitCode.PREFLIGHT,'unexpected_account_metadata')


@contextmanager
def preserve_account_references():
    before=_preference()
    identifiers=_identifiers(before)
    if not identifiers:
        raise CliError('restore the account once in Xcode Settings > Accounts; CLI reuses that session',
                       ExitCode.NEEDS_USER,'xcode_account_missing')
    _validate_metadata(before)
    base=Path.home()/'Library/Application Support/SideloadlyCmd/xcode-account-backups'
    if base.is_symlink(): raise OSError('account backup directory cannot be a symbolic link')
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    if base.stat().st_uid!=os.getuid(): raise OSError('account backup directory must be owned by current user')
    os.chmod(base,0o700)
    backup=Path(tempfile.mkdtemp(prefix='build-',dir=base))/'account-references.json'
    write_json(backup,{'schema':1,'account_references':before})
    try:
        yield
    finally:
        current=_preference()
        current_ids=_identifiers(current)
        if not current_ids:
            _preference(before,write=True)
            if _preference()!=before:
                raise CliError('Xcode account reference restoration was not verified; private snapshot retained',
                               ExitCode.PREFLIGHT,'xcode_account_restore_unverified')
            raise CliError('Xcode build cleared account references; references were restored and this operation stopped',
                           ExitCode.NEEDS_USER,'xcode_account_context_lost')
        if not identifiers.issubset(current_ids):
            raise CliError('configured Xcode accounts changed during the build; current settings were preserved',
                           ExitCode.NEEDS_USER,'xcode_account_context_changed')
