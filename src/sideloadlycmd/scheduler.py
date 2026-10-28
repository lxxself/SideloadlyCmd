"""User-domain launchd scheduling for the private native refresh registry."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import sys
import time

from .errors import CliError, ExitCode
from .refresh import _root
from .xcode_backend import _run

LABEL = 'org.sideloadlycmd.native-refresh'
NATIVE_PATH = '/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin'
LEGACY_PATH = '/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin'


def definition(state: Path, launcher: Path) -> dict:
    state = state.expanduser().resolve()
    launcher = launcher.resolve()
    if not launcher.is_file():
        launcher=Path(__file__).with_name('_entry.py').resolve()
    if not launcher.is_file():
        raise CliError('bundled native scheduler entry is missing', ExitCode.PREFLIGHT, 'launcher_missing')
    environment = {'PATH': NATIVE_PATH}
    if os.environ.get('DEVELOPER_DIR'):
        environment['DEVELOPER_DIR'] = os.environ['DEVELOPER_DIR']
    return {'Label': LABEL,
            'ProgramArguments': [str(Path(sys.executable).resolve()), str(launcher), 'refresh-native',
                                 '--state-dir', str(state.resolve()), '--timeout', '900', '--json'],
            'StartInterval': 3600, 'RunAtLoad': False, 'ProcessType': 'Background',
            'LimitLoadToSessionType': 'Background',
            'Umask': 0o077, 'WorkingDirectory': str(launcher.parent),
            'EnvironmentVariables': environment,
            'StandardOutPath': str(state / 'scheduler-result.json'),
            'StandardErrorPath': str(state / 'scheduler-events.jsonl')}


def compatible(actual: dict, expected: dict) -> bool:
    if actual==expected: return True
    legacy=dict(expected)
    legacy['EnvironmentVariables']={**expected['EnvironmentVariables'],'PATH':LEGACY_PATH}
    return actual==legacy


def manage(action: str, state: Path, launcher: Path, timeout: float, *, persist: bool = False) -> dict:
    deadline = time.monotonic() + timeout
    root = _root(state)
    domain = 'user/' + str(os.getuid())
    target = domain + '/' + LABEL
    plist = root / 'native-refresh.plist'
    persistent = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    expected = definition(root, launcher)
    current = _run(['/bin/launchctl', 'print', target], deadline)
    loaded_path = next((line.strip()[7:] for line in current.stdout.decode(errors='replace').splitlines()
                        if line.strip().startswith('path = ')), None)
    owned_paths = {str(plist), str(persistent)}
    if current.returncode == 0 and loaded_path not in owned_paths:
        raise CliError('loaded native scheduler belongs to a different state directory',
                       ExitCode.PREFLIGHT, 'scheduler_state_mismatch')
    if persistent.is_symlink():
        raise CliError('persistent scheduler cannot be a symbolic link', ExitCode.PREFLIGHT, 'unsafe_scheduler_path')
    if persistent.exists() and (persistent.stat().st_uid != os.getuid() or
                                not compatible(plistlib.loads(persistent.read_bytes()),expected)):
        if persist or loaded_path == str(persistent) or action == 'disable':
            raise CliError('persistent native scheduler differs from this state and launcher',
                           ExitCode.PREFLIGHT, 'scheduler_state_mismatch')

    if action == 'enable':
        if persist and not persistent.exists():
            persistent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(persistent, flags, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                plistlib.dump(expected, stream)
        if current.returncode == 0:
            # Do not claim that another state's loaded service is ours.
            if not Path(loaded_path).is_file() or not compatible(plistlib.loads(Path(loaded_path).read_bytes()),expected):
                raise CliError('a native refresh scheduler is already loaded; disable its original state first',
                               ExitCode.PREFLIGHT, 'scheduler_already_loaded')
        else:
            from tempfile import NamedTemporaryFile
            with NamedTemporaryFile(dir=root, prefix='.launchd-', delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                plistlib.dump(definition(root, launcher), stream)
            try:
                os.replace(temporary, plist)
            finally:
                temporary.unlink(missing_ok=True)
            result = _run(['/bin/launchctl', 'bootstrap', domain, str(persistent if persist else plist)], deadline)
            if result.returncode:
                raise CliError('launchd could not load the user-domain refresh job', ExitCode.PREFLIGHT, 'scheduler_bootstrap_failed')
            if _run(['/bin/launchctl', 'print', target], deadline).returncode:
                raise CliError('native refresh scheduler loading was not verified', ExitCode.PARTIAL, 'scheduler_unverified')
        loaded = True
    elif action == 'disable':
        if current.returncode == 0:
            result = _run(['/bin/launchctl', 'bootout', target], deadline)
            if result.returncode or _run(['/bin/launchctl', 'print', target], deadline).returncode == 0:
                raise CliError('native scheduler removal could not be verified', ExitCode.PARTIAL, 'scheduler_unverified')
        persistent.unlink(missing_ok=True)
        loaded = False
    else:
        loaded = current.returncode == 0
    return {'command': 'refresh-scheduler', 'state': 'scheduler_loaded' if loaded else 'scheduler_not_loaded',
            'domain': 'user', 'interval_seconds': 3600, 'loaded': loaded,
            'configuration_current': (plistlib.loads(Path(loaded_path).read_bytes())==expected) if loaded and loaded_path else True,
            'persistence': 'launchagent_configured_reboot_unverified' if persistent.is_file() else 'current_user_domain_lifetime',
            'message': 'checks hourly; only due registered apps are attempted; expiry extension remains independently verified'}
