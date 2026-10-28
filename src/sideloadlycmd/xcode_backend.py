"""Native Xcode-account signing backend. Never exports a private key or drives UI."""
from __future__ import annotations

from pathlib import Path
import copy
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any, Callable

from .errors import CliError, ExitCode
from .private_state import write_json
from .components import plan_components,apply_components
from .xcode_account_state import preserve_account_references
from .ipa import inspect_ipa, IPAError, _safe_extract_app
from .headless import resolve_device
from .progress import stage


def create_project(root: Path, team: str, bundle_id: str, profile_name: str, automatic: bool = False):
    project = root / 'NativeProvisioning.xcodeproj'
    project.mkdir()
    (root / 'main.c').write_text('int main(void) { return 0; }\n')
    settings = {'SDKROOT': 'iphoneos', 'IPHONEOS_DEPLOYMENT_TARGET': '15.0',
                'PRODUCT_NAME': 'NativeProvisioning', 'PRODUCT_BUNDLE_IDENTIFIER': bundle_id,
                'DEVELOPMENT_TEAM': team, 'CODE_SIGN_STYLE': 'Manual',
                'CODE_SIGN_IDENTITY': 'Apple Development', 'PROVISIONING_PROFILE_SPECIFIER': profile_name,
                'GENERATE_INFOPLIST_FILE': 'YES', 'TARGETED_DEVICE_FAMILY': '1,2'}
    if automatic:
        settings['CODE_SIGN_STYLE'] = 'Automatic'
        settings.pop('PROVISIONING_PROFILE_SPECIFIER')
    encoded = ' '.join(k + ' = ' + json.dumps(v) + ';' for k, v in settings.items())
    project_file = '''// !$*UTF8*$!
{archiveVersion = 1; classes = {}; objectVersion = 56; objects = {
A00000000000000000000001 = {isa = PBXProject; buildConfigurationList = A00000000000000000000002; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base); mainGroup = A00000000000000000000003; productRefGroup = A00000000000000000000004; projectDirPath = ""; projectRoot = ""; targets = (A00000000000000000000005);};
A00000000000000000000002 = {isa = XCConfigurationList; buildConfigurations = (A00000000000000000000006); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug;};
A00000000000000000000003 = {isa = PBXGroup; children = (A00000000000000000000007,A00000000000000000000004); sourceTree = "<group>";};
A00000000000000000000004 = {isa = PBXGroup; children = (A00000000000000000000008); name = Products; sourceTree = "<group>";};
A00000000000000000000005 = {isa = PBXNativeTarget; buildConfigurationList = A00000000000000000000009; buildPhases = (A0000000000000000000000A); buildRules = (); dependencies = (); name = NativeProvisioning; productName = NativeProvisioning; productReference = A00000000000000000000008; productType = "com.apple.product-type.application";};
A00000000000000000000006 = {isa = XCBuildConfiguration; buildSettings = {}; name = Debug;};
A00000000000000000000007 = {isa = PBXFileReference; lastKnownFileType = sourcecode.c.c; path = main.c; sourceTree = "<group>";};
A00000000000000000000008 = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = NativeProvisioning.app; sourceTree = BUILT_PRODUCTS_DIR;};
A00000000000000000000009 = {isa = XCConfigurationList; buildConfigurations = (A0000000000000000000000B); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug;};
A0000000000000000000000A = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (A0000000000000000000000C); runOnlyForDeploymentPostprocessing = 0;};
A0000000000000000000000B = {isa = XCBuildConfiguration; buildSettings = {SETTINGS}; name = Debug;};
A0000000000000000000000C = {isa = PBXBuildFile; fileRef = A00000000000000000000007;};
}; rootObject = A00000000000000000000001;}
'''.replace('SETTINGS', encoded)
    (project / 'project.pbxproj').write_text(project_file)
    return project



def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CliError('native operation exceeded its total timeout', ExitCode.TIMEOUT, 'timeout')
    return remaining


def _run(command: list[str], deadline: float, *, data: bytes | None = None) -> subprocess.CompletedProcess:
    import signal
    process = None
    _remaining(deadline)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                   env={k: v for k, v in os.environ.items()
                                        if not k.startswith(("DYLD_", "PYTHON"))})
        stdout, stderr = process.communicate(input=data, timeout=_remaining(deadline))
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        raise CliError('native command exceeded its total timeout', ExitCode.TIMEOUT, 'timeout') from exc
    except OSError as exc:
        raise CliError('required native command is unavailable', ExitCode.PREFLIGHT, 'native_tool_unavailable') from exc
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def select_account(account: str, team: str) -> str:
    path = Path.home() / 'Library/Preferences/com.apple.dt.Xcode.plist'
    try:
        prefs = plistlib.loads(path.read_bytes())
        identifiers = {item['identifier'] for values in prefs.get('DVTDeveloperAccountManagerAppleIDLists', {}).values()
                       for item in values if isinstance(item, dict) and isinstance(item.get('identifier'), str)}
        membership = prefs.get('IDEProvisioningTeamByIdentifier', {})
        matches = [identifier for identifier in identifiers if any(
            isinstance(item, dict) and item.get('teamID') == team for item in membership.get(identifier, []))]
    except (OSError, ValueError, TypeError, KeyError):
        matches = []
    # xcodebuild selects by team. Refuse ambiguity rather than claiming control
    # of which account it will use when multiple saved accounts own that team.
    if not matches:
        raise CliError('no configured Xcode account owns the requested team; existing profiles do not prove a reusable account session',
                       ExitCode.NEEDS_USER, 'xcode_account_missing')
    if len(matches) != 1 or account not in ('xcode', 'xcode:' + matches[0]):
        raise CliError('select the unique saved Xcode account for this team using --account xcode or its xcode:identifier',
                       ExitCode.NEEDS_USER, 'xcode_account_not_unique')
    return matches[0]


def _identity_hashes(deadline: float) -> set[str]:
    result = _run(['/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning'], deadline)
    if result.returncode:
        raise CliError('could not read available signing identities', ExitCode.PREFLIGHT, 'signing_identity_unavailable')
    return set(re.findall(r'\)\s+([A-F0-9]{40})\s+"', result.stdout.decode(errors='replace')))


def _decode_profile(path: Path, deadline: float) -> dict[str, Any] | None:
    result = _run(['/usr/bin/security', 'cms', '-D', '-i', str(path)], deadline)
    if result.returncode:
        return None
    try:
        data = plistlib.loads(result.stdout)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _profile_usable(data: dict[str, Any], team: str, bundle_id: str, udid: str,
                    identities: set[str]) -> str | None:
    expiration = data.get('ExpirationDate')
    if not isinstance(expiration, datetime):
        return None
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if expiration <= datetime.now(timezone.utc):
        return None
    teams, devices = data.get('TeamIdentifier'), data.get('ProvisionedDevices')
    entitlements, certificates = data.get('Entitlements'), data.get('DeveloperCertificates')
    if not all(isinstance(value,list) for value in (teams,devices,certificates)) or not isinstance(entitlements,dict):
        return None
    if team not in teams or udid not in devices:
        return None
    entitlement = entitlements.get('application-identifier', '')
    if not isinstance(entitlement, str) or not fnmatch.fnmatchcase(team + '.' + bundle_id, entitlement):
        return None
    for certificate in certificates:
        if isinstance(certificate, bytes):
            fingerprint = hashlib.sha1(certificate).hexdigest().upper()
            if fingerprint in identities:
                return fingerprint
    return None


@contextmanager
def _renew_profile_cache(roots: list[Path], team: str, bundle_id: str, udid: str,
                         identities: set[str], deadline: float):
    """Hide only this app's exact cache entries, retaining private recovery copies.

    Apple documents removing eligible local cached profiles before another
    automatic-signing build. Wildcard/shared profiles are never removed here.
    """
    matches = []
    for root in roots:
        for path in sorted(root.glob('*.mobileprovision')):
            _remaining(deadline)
            if path.is_symlink() or path.stat().st_uid != os.getuid():
                continue
            data = _decode_profile(path,deadline)
            if (data and _profile_usable(data,team,bundle_id,udid,identities) and
                    data['Entitlements'].get('application-identifier') == team+'.'+bundle_id):
                matches.append(path)
    if not matches:
        yield
        return
    base = Path.home()/'Library/Application Support/SideloadlyCmd/profile-cache-backups'
    if base.is_symlink():
        raise CliError('profile backup directory cannot be a symbolic link',ExitCode.PREFLIGHT,'unsafe_profile_backup')
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    if base.stat().st_uid != os.getuid():
        raise CliError('profile backup directory must be owned by current user',ExitCode.PREFLIGHT,'unsafe_profile_backup')
    os.chmod(base,0o700)
    backup = Path(tempfile.mkdtemp(prefix='renew-',dir=base))
    entries = []
    journal = backup/'manifest.json'
    try:
        for index,path in enumerate(matches):
            _remaining(deadline)
            content = path.read_bytes()
            saved = backup/(str(index)+'.mobileprovision')
            with saved.open('xb') as stream:
                os.chmod(saved,0o600)
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            entries.append({'original':str(path),'backup':saved.name,'sha256':hashlib.sha256(content).hexdigest()})
            write_json(journal,{'state':'cache_refresh_started','profiles':entries})
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entries[-1]['sha256']:
                raise CliError('profile cache changed during refresh; refusing to remove it',ExitCode.PREFLIGHT,'profile_cache_changed')
            path.unlink()
        yield
        write_json(journal,{'state':'cache_refresh_completed','profiles':entries})
    except BaseException:
        conflicts = False
        for entry in entries:
            original = Path(entry['original'])
            if original.exists() or original.is_symlink():
                conflicts = True
                continue
            try:
                content = (backup/entry['backup']).read_bytes()
                if hashlib.sha256(content).hexdigest() != entry['sha256']:
                    conflicts = True
                    continue
                with original.open('xb') as stream:
                    os.chmod(original,0o600)
                    stream.write(content); stream.flush(); os.fsync(stream.fileno())
            except OSError:
                conflicts = True
        try:
            write_json(journal,{'state':'restored_after_failure','restore_conflicts':conflicts,'profiles':entries})
        except OSError:
            pass
        raise


def _provisioning_diagnostics(result: subprocess.CompletedProcess) -> list[str]:
    text=(result.stdout+result.stderr).decode(errors='replace')
    return [name for name,marker in {
        'missing_account_mapping':'Xcode-Username',
        'missing_account_token':'Xcode-Token',
        'missing_account_dsid':'Xcode-AlternateDSID',
        'account_credentials_error':'DVTDeveloperAccountCredentialsError',
        'interaction_not_allowed':'-25308',
        'missing_keychain_entitlement':'-34018',
        'no_accounts_loaded':'No Accounts',
        'account_session_expired':'session has expired',
        'keychain_signing_failed':'errSecInternalComponent',
        'signing_interaction_required':'User interaction is not allowed',
        'destination_unavailable':'Unable to find a destination',
    }.items() if marker in text]


def _provisioning_build(command: list[str], deadline: float) -> subprocess.CompletedProcess:
    result=None
    try:
        with preserve_account_references():
            result=_run(command,deadline)
    except CliError as exc:
        if result is not None:
            diagnostics=_provisioning_diagnostics(result)
            if diagnostics:
                raise CliError(exc.message+'; native diagnostic: '+','.join(diagnostics),exc.code,exc.category) from exc
        raise
    return result


def obtain_profile(team: str, bundle_id: str, udid: str, deadline: float,
                   workspace: Path, renew: bool = False, *, offline: bool = False) -> tuple[Path, dict[str, Any], str]:
    roots = [Path.home() / 'Library/Developer/Xcode/UserData/Provisioning Profiles',
             Path.home() / 'Library/MobileDevice/Provisioning Profiles']
    identities = _identity_hashes(deadline)
    # Existing matching material requires no remote changes and no new login.
    for root in ([] if renew else roots):
        for path in sorted(root.glob('*.mobileprovision')):
            data = _decode_profile(path, deadline)
            identity = _profile_usable(data, team, bundle_id, udid, identities) if data else None
            if identity:
                return path, data, identity
    if offline:
        raise CliError('offline preparation requires a valid cached profile for every executable bundle and this device',
                       ExitCode.PREFLIGHT, 'cached_profile_missing')
    cache_refresh = _renew_profile_cache(roots,team,bundle_id,udid,identities,deadline) if renew else nullcontext()
    with cache_refresh:
        target = workspace / ('provision-' + hashlib.sha256(bundle_id.encode()).hexdigest()[:16])
        target.mkdir(mode=0o700)
        project = create_project(target, team, bundle_id, '', automatic=True)
        result = _provisioning_build(['/usr/bin/xcodebuild', '-project', str(project), '-scheme', 'NativeProvisioning',
                       '-configuration', 'Debug', '-sdk', 'iphoneos', '-destination', 'id=' + udid,
                       '-allowProvisioningUpdates', '-allowProvisioningDeviceRegistration',
                       'SYMROOT=' + str(target / 'build'), 'OBJROOT=' + str(target / 'objects'), 'build'], deadline)
        if result.returncode:
            diagnostics = _provisioning_diagnostics(result)
            message = 'Xcode could not prepare the profile for the exact requested bundle and team'
            if diagnostics:
                message += '; native diagnostic: ' + ','.join(diagnostics)
            raise CliError(message, ExitCode.NEEDS_USER, 'xcode_provisioning_failed')
        profiles = list((target / 'build').rglob('embedded.mobileprovision'))
        if len(profiles) != 1:
            raise CliError('Xcode did not produce one identifiable profile', ExitCode.UNKNOWN, 'profile_unverified')
        data = _decode_profile(profiles[0], deadline)
        identities = _identity_hashes(deadline)
        identity = _profile_usable(data, team, bundle_id, udid, identities) if data else None
        if not identity:
            raise CliError('profile does not bind the requested device, team, bundle and available private key',
                           ExitCode.UNKNOWN, 'profile_identity_mismatch')
        return profiles[0], data, identity


def _rewrite_identifiers(value: Any, source: str, target: str) -> Any:
    if isinstance(value, str):
        if value == source or value.startswith(source + '.'):
            return target + value[len(source):]
    elif isinstance(value, list):
        return [_rewrite_identifiers(item, source, target) for item in value]
    elif isinstance(value, dict):
        return {key: _rewrite_identifiers(item, source, target) for key, item in value.items()}
    return value


def _is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                                  b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                                  b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def require_global_xcode(deadline: float) -> None:
    result=_run(['/usr/bin/env','-u','DEVELOPER_DIR','/usr/bin/xcode-select','-p'],deadline)
    selected=result.stdout.decode(errors='replace').strip()
    if result.returncode or not selected.endswith('/Contents/Developer'):
        raise CliError('online provisioning requires global xcode-select to point to full Xcode; verify env -u DEVELOPER_DIR xcode-select -p before downloading platform components',
                       ExitCode.PREFLIGHT,'global_xcode_not_selected')


def prepare(ipa: Path, output: Path, account: str, team: str, bundle_id: str,
            bundle_id_mode: str, device: str, strip_watch: bool, timeout: float, *, renew_profiles: bool = False, offline: bool = False, strip_extensions: list[str] | None = None, strict_components: bool = False, component_exclusions: list[str] | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise CliError('Team ID must contain 10 uppercase letters or digits', ExitCode.USAGE, 'invalid_team')
    if offline and renew_profiles:
        raise CliError('offline preparation cannot renew profiles', ExitCode.USAGE, 'offline_renewal_conflict')
    if not offline:
        require_global_xcode(deadline)
        select_account(account, team)
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise CliError('preparation output already exists; choose a new directory', ExitCode.USAGE, 'output_exists')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage("prepare_device", "Checking the device before preparation")
    udid = resolve_device(device, deadline)
    with tempfile.TemporaryDirectory(prefix='.sideloadly-prepare-', dir=output.parent) as tmp:
        work = Path(tmp)
        stage('ipa_snapshot', 'Copying and validating the source IPA')
        snapshot = work / 'source.ipa'
        with ipa.expanduser().open('rb') as source, snapshot.open('xb') as target:
            os.chmod(snapshot, 0o600)
            while block := source.read(1024 * 1024):
                _remaining(deadline)
                target.write(block)
        try:
            info = inspect_ipa(snapshot, timeout_seconds=_remaining(deadline))
        except IPAError as exc:
            raise CliError(str(exc), ExitCode.INVALID_IPA, 'invalid_ipa') from exc
        if info.bundle_id != bundle_id or info.encryption_status != 'not_encrypted':
            raise CliError('IPA bundle or confirmed encryption state differs from the requested input',
                           ExitCode.INVALID_IPA, 'ipa_identity_mismatch')
        final_id = bundle_id if bundle_id_mode == 'exact' else bundle_id + '.' + team
        ready = work / 'ready'
        ready.mkdir(mode=0o700)
        stage('ipa_extraction', 'Extracting the app and planning optional components')
        with zipfile.ZipFile(snapshot) as archive:
            app = _safe_extract_app(archive, info.app_directory, ready, deadline)
        components=plan_components(snapshot,info.app_directory,strip_watch,strip_extensions,strict_components,component_exclusions,deadline)
        apply_components(app,components)
        for warning in components['warnings']:
            stage('component_removed', warning)
        app_bundles = [app]
        for path in app.rglob('Info.plist'):
            if path.parent == app:
                continue
            metadata = plistlib.loads(path.read_bytes())
            if metadata.get('CFBundlePackageType') in ('APPL', 'XPC!'):
                app_bundles.append(path.parent)
        bindings: dict[Path, tuple[dict[str, Any], str]] = {}
        profiles_summary = []
        for bundle_index, bundle in enumerate(app_bundles, 1):
            stage("provisioning", "Obtaining and validating a provisioning profile", current=bundle_index, total=len(app_bundles))
            metadata_path = bundle / 'Info.plist'
            metadata = plistlib.loads(metadata_path.read_bytes())
            old_id = metadata.get('CFBundleIdentifier', '')
            if old_id != bundle_id and not old_id.startswith(bundle_id + '.'):
                raise CliError('nested executable bundle does not belong to the requested app',
                               ExitCode.INVALID_IPA, 'nested_bundle_mismatch')
            target_id = final_id + old_id[len(bundle_id):]
            metadata = _rewrite_identifiers(metadata, bundle_id, final_id)
            metadata_path.write_bytes(plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY))
            profile, data, identity = obtain_profile(team, target_id, udid, deadline, work, renew=renew_profiles, offline=offline)
            shutil.copyfile(profile, bundle / 'embedded.mobileprovision')
            bindings[bundle] = (data, identity)
            profiles_summary.append({'bundle_id': target_id, 'expires': data['ExpirationDate'].isoformat(),
                                     'profile_uuid': data.get('UUID'), 'signing_identity': identity})
        main_identity = bindings[app][1]
        bundles = {app, *app_bundles}
        executables = set()
        for path in app.rglob('Info.plist'):
            try:
                data = plistlib.loads(path.read_bytes())
            except (ValueError, TypeError):
                continue
            executable = data.get('CFBundleExecutable')
            if isinstance(executable, str) and _is_macho(path.parent / executable):
                bundles.add(path.parent)
                executables.add((path.parent / executable).resolve())
        raw_code = [path for path in app.rglob('*') if _is_macho(path) and path.resolve() not in executables]
        signing_targets = sorted(set(raw_code) | bundles, key=lambda path: len(path.parts), reverse=True)
        for index, target in enumerate(signing_targets):
            stage("codesigning", "Signing app code", current=index + 1, total=len(signing_targets))
            command = ['/usr/bin/codesign', '--force', '--sign', main_identity, '--timestamp=none']
            if target in bindings:
                data, identity = bindings[target]
                command[3] = identity
                actual_id = plistlib.loads((target / 'Info.plist').read_bytes())['CFBundleIdentifier']
                entitlements = copy.deepcopy(data['Entitlements'])
                entitlements['application-identifier'] = team + '.' + actual_id
                entitlements['com.apple.developer.team-identifier'] = team
                groups = entitlements.get('keychain-access-groups')
                if isinstance(groups, list):
                    entitlements['keychain-access-groups'] = [item.replace('*', actual_id) for item in groups]
                entitlements_path = work / f'entitlements-{index}.plist'
                entitlements_path.write_bytes(plistlib.dumps(entitlements))
                os.chmod(entitlements_path, 0o600)
                command += ['--entitlements', str(entitlements_path), '--generate-entitlement-der']
            result = _run(command + [str(target)], deadline)
            if result.returncode:
                errors = result.stderr.decode(errors='replace')
                known = next((marker for marker in (
                    'errSecInternalComponent', 'User interaction is not allowed',
                    'The specified item could not be found in the keychain',
                    'no identity found', 'resource fork, Finder information',
                    'bundle format unrecognized', 'CSSMERR_CSP_NO_USER_INTERACTION') if marker in errors), None)
                detail = ': ' + known if known else ''
                raise CliError('native codesign failed' + detail + '; no installation was started',
                               ExitCode.INSTALL_FAILED, 'signing_failed')
        stage('signature_verification', 'Independently verifying the complete app signature')
        verified = _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], deadline)
        if verified.returncode:
            raise CliError('independent strict signature verification failed', ExitCode.INSTALL_FAILED, 'signature_invalid')
        manifest = {'schema': 1, 'engine': 'xcode', 'state': 'prepared_and_signature_verified',
                    'source_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    'source_bundle_id': bundle_id, 'final_bundle_id': final_id, 'team_id': team,
                    'version': info.version, 'build': info.build, 'app_directory': app.name,
                    'device_sha256': hashlib.sha256(udid.encode()).hexdigest(),
                    'strip_watch': components['strip_watch'], 'strip_watch_requested':strip_watch,
                    'strip_extensions':strip_extensions or [],'strict_components':strict_components,
                    **components, 'profiles': profiles_summary,
                    'provisioning': 'existing_profiles_only' if offline else 'xcode_account',
                    'installation': 'not_attempted', 'refresh': 'not_tested'}
        (ready / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        os.chmod(ready / 'manifest.json', 0o600)
        os.rename(ready, output)
        return {'command': 'prepare', **manifest, 'output': str(output),
                'message': 'signed using verified profiles and an existing Keychain private key; strict verification passed'+(' Component warnings: '+'; '.join(components['warnings']) if components['warnings'] else '')}


def _lookup_installed_app(udid: str, bundle_id: str, deadline: float) -> dict | None:
    try:
        result=_run([sys.executable,'-I',str(Path(__file__).with_name('_installation_query.py'))],deadline,
                    data=json.dumps({'device':udid,'bundle_id':bundle_id}).encode())
        if result.returncode: return None
        value=json.loads(result.stdout).get('app')
        if isinstance(value,dict) and value.get('bundleIdentifier')==bundle_id:
            return value
    except (CliError,OSError,ValueError,TypeError,AttributeError):
        pass
    return None


def install_prepared(prepared: Path, device: str, team: str, bundle_id: str,
                     timeout: float, *, on_install_started: Callable[[], None] | None = None, verify_only: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    stage('prepared_validation', 'Validating prepared app, device binding and signatures')
    prepared = prepared.expanduser().resolve()
    try:
        manifest = json.loads((prepared / 'manifest.json').read_text())
        name = manifest.get('app_directory')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.app'):
            raise ValueError('invalid app name')
        app = prepared / name
        metadata = plistlib.loads((app / 'Info.plist').read_bytes())
    except (OSError, ValueError, TypeError):
        raise CliError('prepared app or manifest is invalid', ExitCode.PREFLIGHT, 'prepared_app_invalid')
    if app.is_symlink() or manifest.get('team_id') != team or manifest.get('final_bundle_id') != bundle_id or \
            metadata.get('CFBundleIdentifier') != bundle_id or metadata.get('CFBundleShortVersionString') != manifest.get('version') or \
            metadata.get('CFBundleVersion') != manifest.get('build'):
        raise CliError('prepared app does not match the requested bundle/team/version', ExitCode.UNKNOWN, 'prepared_identity_mismatch')
    udid = resolve_device(device, deadline)
    if manifest.get('device_sha256') != hashlib.sha256(udid.encode()).hexdigest():
        raise CliError('prepared app was bound to a different device', ExitCode.UNKNOWN, 'prepared_device_mismatch')
    verified = _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], deadline)
    if verified.returncode:
        raise CliError('prepared app signature is no longer valid', ExitCode.INSTALL_FAILED, 'signature_invalid')
    identities = _identity_hashes(deadline)
    with tempfile.TemporaryDirectory(prefix='sideloadly-install-', dir='/private/tmp') as tmp:
        work = Path(tmp)
        # Recheck profiles and the actual leaf signer, not only the sidecar.
        for path in app.rglob('embedded.mobileprovision'):
            data = _decode_profile(path, deadline)
            bundle = path.parent
            actual_id = plistlib.loads((bundle / 'Info.plist').read_bytes()).get('CFBundleIdentifier')
            identity = _profile_usable(data, team, actual_id, udid, identities) if data and isinstance(actual_id, str) else None
            if not identity:
                raise CliError('prepared profile identity or validity check failed', ExitCode.UNKNOWN, 'profile_identity_mismatch')
            prefix = work / ('cert-' + hashlib.sha256(str(bundle).encode()).hexdigest()[:16] + '-')
            cert_result = _run(['/usr/bin/codesign', '-d', '--extract-certificates=' + str(prefix), str(bundle)], deadline)
            leaf = Path(str(prefix) + '0')
            if cert_result.returncode or not leaf.is_file() or leaf.read_bytes() not in data.get('DeveloperCertificates', []):
                raise CliError('app signer does not match its embedded profile', ExitCode.UNKNOWN, 'profile_signer_mismatch')
        receipt = prepared / 'install-receipt.json'
        if verify_only:
            try:
                prior=json.loads(receipt.read_text())
            except (OSError,ValueError):
                raise CliError('read-only recovery requires a completed native install receipt',ExitCode.PREFLIGHT,'install_receipt_missing')
            if (not isinstance(prior,dict) or prior.get('install') not in {'completed_this_run','completed_prior_attempt'} or
                    any(prior.get(key)!=value for key,value in {'final_bundle_id':bundle_id,'team_id':team,
                        'version':manifest['version'],'build':manifest['build']}.items())):
                raise CliError('read-only recovery requires a matching completed install receipt',ExitCode.UNKNOWN,'install_receipt_mismatch')
            installation=prior.get('installation_result_present',False)
        else:
            stage('device_installation', 'Installing the app on the USB device; keep it connected and unlocked')
            installed_output = work / 'install.json'
            result = {'command':'install-prepared', 'state':'install_completion_unknown',
                      'final_bundle_id':bundle_id, 'team_id':team, 'version':manifest['version'], 'build':manifest['build'],
                      'install':'completion_unknown', 'device_app':'unverified', 'refresh':'not_registered_by_native_cli',
                      'message':'installation attempt was started; completion must be established before retrying'}
            # Persist intent before crossing the device-mutation boundary. A crash
            # after deployment must not become an automatic scheduled reinstall.
            write_json(receipt,result)
            if on_install_started is not None:
                on_install_started()
            try:
                installation_process = _run(['/usr/bin/xcrun', 'devicectl', 'device', 'install', 'app', '--device', udid,
                                str(app), '--json-output', str(installed_output), '--timeout',
                                str(max(1, int(_remaining(deadline))))], deadline)
            except CliError as exc:
                result['exit_code'] = int(exc.code)
                result['category'] = 'install_completion_unknown'
                try:
                    write_json(receipt,result)
                except OSError:
                    result['receipt'] = 'not_persisted'
                return result
            if installation_process.returncode:
                result.update({'state':'install_reported_failure', 'install':'reported_failure',
                               'exit_code':int(ExitCode.INSTALL_FAILED), 'category':'native_install_failed',
                               'message':'CoreDevice reported failure; inspect this attempt before retrying'})
                try:
                    write_json(receipt,result)
                except OSError:
                    result['receipt'] = 'not_persisted'
                return result
            result.update({'state':'installed_device_unverified', 'install':'completed_this_run',
                           'message':'CoreDevice completed this install; device and refresh verification remain separate'})
            try:
                write_json(receipt,result)
            except OSError:
                result['receipt'] = 'not_persisted'
            try:
                installation = json.loads(installed_output.read_text())
            except (OSError, ValueError):
                installation = {}
        # The install process supplies fresh completion; device query separately
        # confirms the exact bundle and build, even if this replaces the same version.
        stage('device_verification', 'Querying the installed app version and build')
        apps_output = work / 'apps.json'
        try:
            query = _run(['/usr/bin/xcrun', 'devicectl', 'device', 'info', 'apps', '--device', udid,
                          '--bundle-id', bundle_id, '--json-output', str(apps_output), '--timeout',
                          str(max(1, int(_remaining(deadline))))], deadline)
        except CliError:
            # Preserve the fresh successful installation even when the separate
            # post-install device query cannot finish. Never retry it blindly.
            query = subprocess.CompletedProcess([], 1)
        matches = []
        try:
            data = json.loads(apps_output.read_text()) if query.returncode == 0 else {}
            payload = data.get('result') if isinstance(data,dict) else None
            apps = payload.get('apps') if isinstance(payload,dict) else None
            matches = [row for row in apps if isinstance(row,dict) and row.get('bundleIdentifier') == bundle_id] if isinstance(apps,list) else []
            confirmed = len(matches) == 1 and matches[0].get('version') == manifest['version'] and \
                matches[0].get('bundleVersion') == manifest['build']
        except (OSError, ValueError, TypeError):
            confirmed = False
        verification_engine = 'coredevice' if confirmed else None
        if not confirmed and not matches:
            alternative = _lookup_installed_app(udid,bundle_id,deadline)
            confirmed = bool(alternative and alternative.get('version')==manifest['version'] and
                             alternative.get('bundleVersion')==manifest['build'])
            if confirmed: verification_engine = 'libimobiledevice_installation_proxy'
        result = {'command': 'verify-prepared' if verify_only else 'install-prepared', 'state': 'installed_refresh_unverified' if confirmed else 'installed_device_unverified',
                  'final_bundle_id': bundle_id, 'team_id': team,
                  'version': manifest['version'], 'build': manifest['build'],
                  'install': 'completed_prior_attempt' if verify_only else 'completed_this_run', 'device_app': 'version_and_build_verified' if confirmed else 'unverified',
                  'refresh': 'not_registered_by_native_cli',
                  'installation_result_present': bool(installation), 'verification_engine':verification_engine,
                  'message': 'previous native install was checked without reinstalling; refresh remains separate' if verify_only else 'CoreDevice completed this install; native refresh is a separate acceptance step'}
        try:
            write_json(receipt,result)
        except OSError:
            result['receipt'] = 'not_persisted'
            result['message'] = 'installation completed; private receipt could not be saved, so refresh registration cannot proceed'
        return result
