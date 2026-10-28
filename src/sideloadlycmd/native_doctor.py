"""Read-only native prerequisites; no GUI, login, provisioning or installation."""
from __future__ import annotations
from pathlib import Path
import platform
import plistlib
import re
import shutil
import sys
import tempfile
import time

from .errors import CliError, ExitCode
from .headless import resolve_device
from .xcode_backend import _identity_hashes, _run, select_account, create_project


def destination_status(device: str, team: str, deadline: float) -> dict:
    """Inspect exact Xcode destination eligibility without provisioning updates."""
    with tempfile.TemporaryDirectory(prefix='sideloadly-doctor-') as tmp:
        project=create_project(Path(tmp),team,'com.example.sideloadlycmd.preflight','',automatic=True)
        result=_run(['/usr/bin/xcodebuild','-project',str(project),'-scheme','NativeProvisioning',
                     '-sdk','iphoneos','-showdestinations'],deadline)
    text=(result.stdout+result.stderr).decode(errors='replace')
    selected=[line for line in text.splitlines() if re.search(r'\bid:\s*'+re.escape(device)+r'(?=,|\s|})',line)]
    if result.returncode:
        return {'state':'unavailable','category':'xcode_destinations_failed'}
    if not selected:
        return {'state':'unavailable','category':'xcode_device_unavailable'}
    if any('error:' in line.lower() for line in selected):
        missing=any('is not installed' in line.lower() for line in selected)
        return {'state':'unavailable','category':'xcode_platform_unavailable' if missing else 'xcode_device_ineligible',
                'action':'Xcode reports the platform unavailable; check global xcode-select and Components before downloading anything' if missing
                         else 'Resolve device eligibility in Xcode; no provisioning or installation was attempted'}
    return {'state':'available','provisioning':'not_attempted'}


def diagnose(*,team: str | None=None,account: str='xcode',device: str | None=None,
             offline: bool=False,timeout: float=30) -> tuple[dict,ExitCode]:
    deadline=time.monotonic()+timeout
    checks=[];code=ExitCode.OK
    def add(name,state,required=True,**detail):
        checks.append({'name':name,'state':state,'required':required,**detail})
    def fail(value):
        nonlocal code
        if code==ExitCode.OK or value==ExitCode.TIMEOUT: code=value
    def run(command):
        try: return _run(command,deadline)
        except CliError as error:
            fail(error.code);return None
    add('python','available' if sys.version_info>=(3,10) else 'unsupported',version=platform.python_version())
    if sys.version_info<(3,10): fail(ExitCode.PREFLIGHT)
    if platform.system()!='Darwin':
        add('macos','unsupported');fail(ExitCode.PREFLIGHT)
        return {'command':'native-doctor','state':'native_setup_required','checks':checks},code
    add('macos','available',version=platform.mac_ver()[0],architecture=platform.machine())
    developer=run(['/usr/bin/xcode-select','-p'])
    directory=developer.stdout.decode(errors='replace').strip() if developer and developer.returncode==0 else ''
    full=directory.endswith('/Contents/Developer')
    add('full_xcode','available' if full else 'missing',developer_directory=directory or None,
        action=None if full else 'Install full Xcode and select its Contents/Developer directory')
    if not full: fail(ExitCode.PREFLIGHT)
    if full:
        global_selection=run(['/usr/bin/env','-u','DEVELOPER_DIR','/usr/bin/xcode-select','-p'])
        global_directory=global_selection.stdout.decode(errors='replace').strip() if global_selection and global_selection.returncode==0 else None
        global_full=bool(global_directory and global_directory.endswith('/Contents/Developer'))
        add('global_xcode_selection','full_xcode_selected' if global_full else 'needs_setup',
            required=not offline,developer_directory=global_directory,matches_process=global_directory==directory,
            action=None if global_full else 'sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer')
        if not offline and not global_full: fail(ExitCode.PREFLIGHT)
        tool=run(['/usr/bin/xcrun','--find','devicectl'])
        ready=tool is not None and tool.returncode==0
        add('devicectl','available' if ready else 'missing')
        if not ready: fail(ExitCode.PREFLIGHT)
    for command in ('/usr/bin/codesign','/usr/bin/security'):
        ready=Path(command).is_file()
        add(Path(command).name,'available' if ready else 'missing')
        if not ready: fail(ExitCode.PREFLIGHT)
    mobile={name:shutil.which(name) for name in ('idevice_id','ideviceinfo')}
    for name,path in mobile.items():
        add(name,'available' if path else 'missing',path=path,
            action=None if path else 'brew install libimobiledevice')
        if not path: fail(ExitCode.PREFLIGHT)
    try:
        prefs=plistlib.loads((Path.home()/'Library/Preferences/com.apple.dt.Xcode.plist').read_bytes())
        configured={entry.get('identifier') for values in prefs.get('DVTDeveloperAccountManagerAppleIDLists',{}).values()
                    for entry in values if isinstance(entry,dict) and isinstance(entry.get('identifier'),str)}
        membership=prefs.get('IDEProvisioningTeamByIdentifier',{})
        teams=sorted({item['teamID'] for identifier in configured for item in membership.get(identifier,[])
                      if isinstance(item,dict) and isinstance(item.get('teamID'),str) and re.fullmatch('[A-Z0-9]{10}',item['teamID'])})
    except (OSError,ValueError,TypeError,KeyError,AttributeError): configured=set();teams=[]
    if offline:
        add('xcode_saved_session','not_required',required=False)
    else:
        available=bool(configured)
        if team:
            try: select_account(account,team)
            except CliError: available=False
        add('xcode_saved_session','configured' if available else 'needs_setup',count=len(configured),team_ids=teams,
            online_authentication='not_tested',action=None if available else 'Sign in once in Xcode Settings > Accounts; select the intended team')
        if not available: fail(ExitCode.NEEDS_USER)
    try: identities=_identity_hashes(deadline)
    except CliError as error: identities=set();fail(error.code)
    add('local_signing_identity','available' if identities else 'needs_setup',count=len(identities),
        signing_access='not_tested',action=None if identities else 'Create an Apple Development certificate on this Mac through Xcode; do not revoke another Mac certificate automatically')
    if not identities: fail(ExitCode.NEEDS_USER)
    if mobile['idevice_id']:
        devices=run([mobile['idevice_id'],'-l'])
        count=len(devices.stdout.decode(errors='replace').strip().splitlines()) if devices and devices.returncode==0 else None
        add('usb_devices','available' if count else 'not_connected',required=bool(device),count=count)
        if device:
            try:
                selected=resolve_device(device,deadline)
                add('requested_device','matched_one_usb_device')
                if full:
                    eligibility=destination_status(selected,team or '0000000000',deadline)
                    add('xcode_destination',**eligibility)
                    if eligibility['state']!='available': fail(ExitCode.PREFLIGHT)
            except CliError as error:
                add('requested_device','unavailable',category=error.category);fail(error.code)
    elif device:
        add('requested_device','not_checked');fail(ExitCode.PREFLIGHT)
    result={'command':'native-doctor','state':'native_prerequisites_available' if code==ExitCode.OK else 'native_setup_required',
            'checks':checks,'online_authentication':'not_tested','signing':'not_attempted','installation':'not_attempted',
            'message':'read-only prerequisites and saved metadata; actual profile requests and device installation require separate acceptance'}
    return result,code
