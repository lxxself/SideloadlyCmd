"""Terminal-only unlock in launchd's Background security session.

Passwords travel through an owned private Unix socket, never argv, environment,
files or logs. No keychain ACL, partition list or lock settings are changed.
"""
from __future__ import annotations
import ctypes
import getpass
import json
import os
from pathlib import Path
import plistlib
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import warnings


def _peer_uid(connection: socket.socket) -> int:
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    libc.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    if libc.getpeereid(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid)):
        raise OSError('could not verify local peer')
    return uid.value


def _receive(connection: socket.socket, length: int) -> bytearray:
    data = bytearray(length)
    view = memoryview(data)
    offset = 0
    while offset < length:
        count = connection.recv_into(view[offset:])
        if not count:
            raise OSError('local peer disconnected')
        offset += count
    return data


def _security():
    lib = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    lib.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_bool]
    if lib.SecKeychainSetUserInteractionAllowed(False) != 0:
        raise OSError('could not disable keychain UI')
    lib.SecKeychainGetStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.SessionGetInfo.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    lib.SecKeychainUnlock.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_bool]
    lib.SecKeychainUnlock.restype = ctypes.c_int32
    return lib


def _status(lib) -> dict:
    flags, session, attributes = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32()
    keychain_code = lib.SecKeychainGetStatus(None,ctypes.byref(flags))
    session_code = lib.SessionGetInfo(0xffffffff,ctypes.byref(session),ctypes.byref(attributes))
    return {'keychain_status_code':keychain_code,
            'unlocked':bool(flags.value & 1) if keychain_code == 0 else None,
            'session_status_code':session_code,
            'root_security_session':bool(attributes.value & 0x1) if session_code == 0 else None,
            'tty_available':bool(attributes.value & 0x20) if session_code == 0 else None,
            'remote_security_session':bool(attributes.value & 0x1000) if session_code == 0 else None,
            'graphic_access':bool(attributes.value & 0x10) if session_code == 0 else None}


def _send_json(connection, data):
    encoded = json.dumps(data).encode()
    connection.sendall(struct.pack('!I',len(encoded))+encoded)


def _read_json(connection):
    length = struct.unpack('!I',_receive(connection,4))[0]
    if length > 4096:
        raise ValueError('invalid local reply')
    return json.loads(_receive(connection,length))


def worker(path: str, timeout: float) -> None:
    lib = _security()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(path)
        if _peer_uid(connection) != os.getuid():
            raise OSError('unexpected local peer')
        _send_json(connection,_status(lib))
        length = struct.unpack('!I',_receive(connection,4))[0]
        if length > 4096:
            raise ValueError('invalid credential size')
        secret = _receive(connection,length)
        try:
            code = None
            if length:
                buffer = (ctypes.c_char * length).from_buffer(secret)
                code = lib.SecKeychainUnlock(None,length,buffer,True)
            _send_json(connection,{'unlock_status_code':code,**_status(lib)})
        finally:
            secret[:] = b'\0' * len(secret)


def manage(action: str, timeout: float) -> dict:
    from .errors import CliError, ExitCode
    from .xcode_backend import _run
    if action == 'unlock' and not sys.stdin.isatty():
        raise CliError('run background-keychain unlock in a terminal; never pass a password in arguments',
                       ExitCode.NEEDS_USER,'terminal_required')
    deadline = time.monotonic()+timeout
    label = 'org.sideloadlycmd.keychain.'+uuid.uuid4().hex
    domain = 'user/'+str(os.getuid())
    with tempfile.TemporaryDirectory(prefix='slkc-',dir='/private/tmp') as tmp:
        root = Path(tmp)
        path = root/'ipc'
        plist = root/'job.plist'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
            server.bind(str(path)); os.chmod(path,0o600); server.listen(1)
            server.settimeout(max(.001,deadline-time.monotonic()))
            plist.write_bytes(plistlib.dumps({'Label':label,'LimitLoadToSessionType':'Background','RunAtLoad':True,
                'Umask':0o077,'ProgramArguments':[str(Path(sys.executable).resolve()),str(Path(__file__).resolve()),
                                                  '--worker',str(path),'--timeout',str(timeout)]}))
            loaded = _run(['/bin/launchctl','bootstrap',domain,str(plist)],deadline)
            if loaded.returncode:
                raise CliError('could not start private background keychain helper',ExitCode.PREFLIGHT,'keychain_helper_unavailable')
            try:
                with server.accept()[0] as connection:
                    connection.settimeout(max(.001,deadline-time.monotonic()))
                    if _peer_uid(connection) != os.getuid():
                        raise CliError('background keychain peer did not match current user',ExitCode.PREFLIGHT,'keychain_peer_mismatch')
                    before = _read_json(connection)
                    secret = bytearray()
                    try:
                        if action == 'unlock' and before.get('unlocked') is not True:
                            def expired(signum, frame):
                                raise TimeoutError('terminal input timed out')
                            previous = signal.signal(signal.SIGALRM,expired)
                            previous_timer = signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
                            try:
                                with warnings.catch_warnings():
                                    warnings.simplefilter('error',getpass.GetPassWarning)
                                    try:
                                        password = getpass.getpass('Mac login keychain password (not Apple ID): ')
                                    except getpass.GetPassWarning as exc:
                                        raise CliError('terminal cannot hide password input; no password was read',
                                                       ExitCode.NEEDS_USER,'private_terminal_required') from exc
                                secret = bytearray(password.encode('utf-8')); del password
                            finally:
                                signal.setitimer(signal.ITIMER_REAL,*previous_timer)
                                signal.signal(signal.SIGALRM,previous)
                        if len(secret)>4096:
                            raise CliError('keychain password exceeds supported size',ExitCode.USAGE,'credential_size_invalid')
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('background keychain deadline expired')
                        connection.settimeout(remaining)
                        connection.sendall(struct.pack('!I',len(secret)))
                        connection.sendall(secret)
                        after = _read_json(connection)
                    finally:
                        secret[:] = b'\0'*len(secret)
                if action == 'unlock' and after.get('unlocked') is not True:
                    raise CliError('background keychain remains locked; no signing or installation was attempted',
                                   ExitCode.NEEDS_USER,'background_keychain_locked')
                return {'command':'background-keychain','state':'background_keychain_checked','before':before,'after':after,
                        'password_persisted':False,'acl_modified':False,
                        'message':'default keychain status in launchd Background security session; signing access is a separate check'}
            except (OSError,ValueError) as exc:
                raise CliError('private background keychain exchange failed or timed out; no values were logged',
                               ExitCode.NEEDS_USER,'keychain_exchange_failed') from exc
            finally:
                # Cleanup must not replace the operation result with a traceback
                # or add another full operation timeout after terminal input.
                try:
                    subprocess.run(['/bin/launchctl','bootout',domain+'/'+label],stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,timeout=min(1.0,max(.001,deadline-time.monotonic())))
                except (OSError,subprocess.TimeoutExpired):
                    pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker',required=True)
    parser.add_argument('--timeout',type=float,required=True)
    args = parser.parse_args()
    worker(args.worker,args.timeout)
