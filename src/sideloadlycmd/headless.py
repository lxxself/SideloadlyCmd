"""Process boundary for the independent native CLI backend. No desktop imports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

from .errors import CliError, ExitCode


def validate_backend(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise CliError('native backend is missing or not executable', ExitCode.PREFLIGHT, 'backend_unavailable')
    return path


def run_backend(backend: Path, arguments: list[str], timeout: float, *, state_dir: Path | None = None,
                interactive: bool = False) -> subprocess.CompletedProcess:
    backend = validate_backend(backend)
    if interactive and not sys.stdin.isatty():
        raise CliError('run login in an interactive terminal; never send credentials in command arguments',
                       ExitCode.NEEDS_USER, 'terminal_required')
    env = {k: v for k, v in os.environ.items() if k not in {
        'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH', 'D_FLAGS', 'SIDELOADER_CONFIG_DIR'
    } and not k.startswith('PYTHON')}
    # Skip the upstream re-exec so interactive password input retains /dev/tty.
    env['SIDELOADER_DYLD_SHIM'] = '1'
    env['DYLD_FALLBACK_LIBRARY_PATH'] = '/opt/homebrew/lib:/usr/local/lib:/usr/lib'
    env['NO_COLOR'] = '1'
    if state_dir is not None:
        state_dir = state_dir.expanduser()
        if state_dir.is_symlink():
            raise CliError('CLI state directory cannot be a symbolic link', ExitCode.PREFLIGHT, 'unsafe_state_directory')
        state_dir = state_dir.resolve()
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state_dir.is_symlink() or state_dir.stat().st_uid != os.getuid():
            raise CliError('CLI state directory must be owned by the current user', ExitCode.PREFLIGHT, 'unsafe_state_directory')
        os.chmod(state_dir, 0o700)
        env['SIDELOADER_CONFIG_DIR'] = str(state_dir)
    command = [str(backend), '--json', '--log-level', 'error', *arguments]
    previous_umask = os.umask(0o077)
    try:
        process = subprocess.Popen(command, env=env, start_new_session=not interactive,
                                   stdin=None if interactive else subprocess.DEVNULL,
                                   stdout=None if interactive else subprocess.PIPE,
                                   stderr=None if interactive else subprocess.PIPE, text=True)
    except OSError as exc:
        raise CliError('could not start native backend', ExitCode.PREFLIGHT, 'backend_unavailable') from exc
    finally:
        os.umask(previous_umask)

    def stop() -> None:
        try:
            if interactive:
                if process.poll() is None:
                    process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stop()
        raise CliError('native backend exceeded its total time limit', ExitCode.TIMEOUT, 'timeout') from exc
    except BaseException:
        stop()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout or '', stderr or '')


def doctor(backend: Path, timeout: float) -> dict[str, Any]:
    path = validate_backend(backend)
    result = run_backend(path, ['native-anisette-check'], timeout)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        data = {}
    ready = result.returncode == 0 and isinstance(data, dict) and data.get('state') == 'native_headers_available'
    if not ready:
        raise CliError('native Anisette is unavailable; no Apple login was attempted', ExitCode.PREFLIGHT, 'native_anisette_unavailable')
    return {'command': 'headless-doctor', 'state': 'native_headers_available',
            'backend_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'authentication': 'not_attempted', 'installation': 'not_attempted',
            'refresh': 'not_tested', 'message': 'local native headers are available; no values were logged'}


def login(backend: Path, state_dir: Path, timeout: float) -> dict[str, Any]:
    result = run_backend(backend, ['--native-anisette', 'login'], timeout,
                         state_dir=state_dir, interactive=True)
    if result.returncode < 0:
        raise CliError(f'native authentication process terminated by signal {-result.returncode}; no installation was started',
                       ExitCode.UNKNOWN, 'backend_crashed')
    if result.returncode != 0:
        raise CliError(f'native login failed (backend exit {result.returncode}); inspect the terminal result',
                       ExitCode.NEEDS_USER, 'authentication_incomplete')
    return {'command': 'login', 'state': 'authenticated', 'account': '<redacted>',
            'installation': 'not_attempted', 'refresh': 'not_tested',
            'message': 'native terminal authentication completed; no certificate or installation command was run'}


def resolve_device(selector: str | None, deadline: float) -> str:
    """Resolve an exact USB UDID or exact device name, without exposing IDs."""
    import shutil
    import time
    device_id = shutil.which('idevice_id')
    device_info = shutil.which('ideviceinfo')
    if not device_id or not device_info:
        raise CliError('libimobiledevice command-line tools are required', ExitCode.PREFLIGHT, 'device_tools_missing')

    def query(command: list[str]) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError('device query timed out', ExitCode.TIMEOUT, 'timeout')
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=min(10.0, remaining))
        except subprocess.TimeoutExpired as exc:
            raise CliError('device query timed out', ExitCode.TIMEOUT, 'timeout') from exc
        except OSError as exc:
            raise CliError('device tools are unavailable', ExitCode.PREFLIGHT, 'device_query_failed') from exc
        if result.returncode:
            raise CliError('could not query USB devices', ExitCode.PREFLIGHT, 'device_query_failed')
        return result.stdout.strip()

    identifiers = query([device_id, '-l']).splitlines()
    if selector is None:
        if not identifiers:
            raise CliError('no USB device is connected; connect and trust the intended iPhone',ExitCode.PREFLIGHT,'device_not_found')
        if len(identifiers)!=1:
            raise CliError('multiple USB devices are connected; specify --device with an exact name or UDID',ExitCode.NEEDS_USER,'device_selection_required')
        return identifiers[0]
    if selector in identifiers:
        return selector
    matches = [identifier for identifier in identifiers
               if query([device_info, '-u', identifier, '-k', 'DeviceName']) == selector]
    if len(matches) != 1:
        raise CliError('target must match one connected USB device by exact name or UDID',
                       ExitCode.PREFLIGHT, 'device_not_unique')
    return matches[0]


def install_plan(backend: Path, state_dir: Path, ipa: Path, device: str, account: str,
                 team: str, bundle_id: str, bundle_id_mode: str, timeout: float) -> dict[str, Any]:
    import time
    from .ipa import IPAError, inspect_ipa
    deadline = time.monotonic() + timeout
    if not all(value.strip() for value in (device, account, team, bundle_id)):
        raise CliError('device, account, team and Bundle ID are required', ExitCode.USAGE, 'missing_identity')
    try:
        info = inspect_ipa(ipa, timeout_seconds=max(0.001, deadline - time.monotonic()))
    except IPAError as exc:
        raise CliError(str(exc), ExitCode.INVALID_IPA, 'invalid_ipa') from exc
    if info.encryption_status != 'not_encrypted':
        raise CliError('IPA encryption state must be confirmed unencrypted', ExitCode.INVALID_IPA, 'unsupported_ipa')
    if info.bundle_id != bundle_id:
        raise CliError('input Bundle ID must match the inspected IPA', ExitCode.USAGE, 'bundle_id_mismatch')
    udid = resolve_device(device, deadline)
    final_id = bundle_id if bundle_id_mode == 'exact' else bundle_id + '.' + team
    arguments = ['--native-anisette', '--account', account, 'install', str(Path(info.path)),
                 '--team', team, '--udid', udid, '--bundle-id', final_id, '--plan-only']
    if bundle_id_mode == 'exact':
        arguments.append('--exact-bundle-id')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CliError('install planning exceeded its total time limit', ExitCode.TIMEOUT, 'timeout')
    result = run_backend(backend, arguments, remaining, state_dir=state_dir)
    try:
        plan = json.loads(result.stdout)
    except (ValueError, TypeError):
        plan = {}
    if result.returncode < 0:
        raise CliError('native planning process crashed; no install was requested', ExitCode.UNKNOWN, 'backend_crashed')
    if result.returncode != 0:
        raise CliError('could not obtain the read-only plan; run terminal login or check the selected account/team',
                       ExitCode.NEEDS_USER, 'install_plan_unavailable')
    if not isinstance(plan, dict) or plan.get('state') != 'read_only_install_plan' or \
            plan.get('teamId') != team or plan.get('sourceBundleId') != bundle_id or \
            plan.get('finalBundleId') != final_id or plan.get('installationAttempted') is not False or \
            plan.get('revokesCertificates') is not False:
        raise CliError('native plan did not confirm the requested identities and scope', ExitCode.UNKNOWN, 'plan_unverified')
    # Only the documented resource summary crosses the process boundary.
    return {'command': 'headless-plan', 'state': 'read_only_install_plan', 'account': '<redacted>',
            'team_id': team, 'source_bundle_id': bundle_id, 'final_bundle_id': final_id,
            'new_app_ids': plan.get('newAppIds'), 'available_app_ids': plan.get('availableAppIds'),
            'device_registration_required': plan.get('deviceRegistrationRequired'),
            'existing_certificates': plan.get('existingCertificates'),
            'may_create_certificate': plan.get('mayCreateCertificate'), 'revokes_certificates': False,
            'installation': 'not_attempted', 'refresh': 'not_tested',
            'message': 'read-only resource plan for the exact requested USB device, account, team and Bundle ID'}
