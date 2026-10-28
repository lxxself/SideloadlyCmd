"""Isolated, read-only installation_proxy lookup. Request arrives on stdin."""
from __future__ import annotations
import ctypes as C
from ctypes.util import find_library
import json
from pathlib import Path
import plistlib
import sys


def library(name: str):
    for directory in ('/opt/homebrew/lib','/usr/local/lib'):
        path=Path(directory)/('lib'+name+'.dylib')
        if path.is_file(): return C.CDLL(str(path))
    path=find_library(name)
    if not path: raise OSError('native library unavailable')
    return C.CDLL(path)


def lookup(device_id: str, bundle_id: str) -> dict | None:
    mobile=library('imobiledevice-1.0')
    plist=library('plist-2.0')
    signatures={'idevice_new_with_options':[C.POINTER(C.c_void_p),C.c_char_p,C.c_int],
        'idevice_free':[C.c_void_p],
        'instproxy_client_start_service':[C.c_void_p,C.POINTER(C.c_void_p),C.c_char_p],
        'instproxy_client_free':[C.c_void_p],
        'instproxy_lookup':[C.c_void_p,C.POINTER(C.c_char_p),C.c_void_p,C.POINTER(C.c_void_p)]}
    for name,args in signatures.items():
        function=getattr(mobile,name);function.argtypes=args;function.restype=C.c_int
    plist.plist_from_xml.argtypes=[C.c_char_p,C.c_uint32,C.POINTER(C.c_void_p)]
    plist.plist_from_xml.restype=None
    plist.plist_to_xml.argtypes=[C.c_void_p,C.POINTER(C.c_void_p),C.POINTER(C.c_uint32)]
    plist.plist_to_xml.restype=None
    plist.plist_free.argtypes=[C.c_void_p];plist.plist_free.restype=None
    libc=C.CDLL('/usr/lib/libSystem.B.dylib');libc.free.argtypes=[C.c_void_p];libc.free.restype=None
    device=C.c_void_p();client=C.c_void_p();options=C.c_void_p();result=C.c_void_p();xml=C.c_void_p()
    try:
        # Public IDEVICE_LOOKUP_USBMUX = 1 << 1. Never select a network device.
        if mobile.idevice_new_with_options(C.byref(device),device_id.encode(),2): return None
        if mobile.instproxy_client_start_service(device,C.byref(client),b'sideloadlycmd-readonly-verification'): return None
        payload=plistlib.dumps({'ReturnAttributes':['CFBundleIdentifier','CFBundleShortVersionString','CFBundleVersion']})
        plist.plist_from_xml(payload,len(payload),C.byref(options))
        if not options.value: return None
        identifiers=(C.c_char_p*2)(bundle_id.encode(),None)
        if mobile.instproxy_lookup(client,identifiers,options,C.byref(result)) or not result.value: return None
        length=C.c_uint32()
        plist.plist_to_xml(result,C.byref(xml),C.byref(length))
        if not xml.value or not 0<length.value<=1024*1024: return None
        data=plistlib.loads(C.string_at(xml,length.value))
        row=data.get(bundle_id) if isinstance(data,dict) else None
        if not isinstance(row,dict) or row.get('CFBundleIdentifier')!=bundle_id: return None
        values=[row.get(key) for key in ['CFBundleIdentifier','CFBundleShortVersionString','CFBundleVersion']]
        if not all(isinstance(value,str) for value in values): return None
        return dict(zip(['bundleIdentifier','version','bundleVersion'],values))
    finally:
        if xml.value: libc.free(xml)
        if result.value: plist.plist_free(result)
        if options.value: plist.plist_free(options)
        if client.value: mobile.instproxy_client_free(client)
        if device.value: mobile.idevice_free(device)


def main():
    try:
        request=json.loads(sys.stdin.buffer.read(8193))
        device,bundle=request['device'],request['bundle_id']
        if not all(isinstance(value,str) and 0<len(value)<=256 and '\x00' not in value for value in (device,bundle)):
            raise ValueError('invalid selector')
        row=lookup(device,bundle)
        print(json.dumps({'engine':'libimobiledevice_installation_proxy','app':row}))
        return 0
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        print(json.dumps({'state':'query_unavailable'}))
        return 10


if __name__=='__main__': raise SystemExit(main())
