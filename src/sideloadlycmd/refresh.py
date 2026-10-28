"""Private, explicit native refresh registrations; independent of Sideloadly's DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from .errors import CliError, ExitCode
from .private_state import write_json as _write
from .headless import resolve_device
from .xcode_backend import install_prepared, prepare, select_account

DEFAULT_STATE = Path.home() / 'Library/Application Support/SideloadlyCmd/native'


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _root(path: Path) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise CliError('native state cannot be a symbolic link', ExitCode.PREFLIGHT, 'unsafe_state_directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise CliError('native state must belong to the current user', ExitCode.PREFLIGHT, 'unsafe_state_directory')
    os.chmod(path, 0o700)
    return path.resolve()



def register(state: Path, prepared: Path, source: Path, device: str, account: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    root = _root(state)
    manifest = json.loads((prepared / 'manifest.json').read_text())
    receipt = json.loads((prepared / 'install-receipt.json').read_text())
    if receipt.get('device_app') != 'version_and_build_verified' or receipt.get('final_bundle_id') != manifest.get('final_bundle_id'):
        raise CliError('native refresh requires a verified native install receipt', ExitCode.PARTIAL, 'install_not_verified')
    udid = resolve_device(device, deadline)
    if hashlib.sha256(udid.encode()).hexdigest() != manifest.get('device_sha256'):
        raise CliError('refresh target differs from the prepared device', ExitCode.UNKNOWN, 'refresh_device_mismatch')
    account_id = select_account(account, manifest['team_id'])
    sources = _root(root / 'sources')
    records = _root(root / 'records')
    # Refresh always uses an immutable private copy of the accepted original IPA,
    # not a path that another build could overwrite before the scheduled run.
    hasher = hashlib.sha256()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=sources, prefix='.source-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            with source.expanduser().open('rb') as input_stream:
                while block := input_stream.read(1024 * 1024):
                    if time.monotonic() >= deadline:
                        temporary.unlink(missing_ok=True)
                        raise CliError('refresh registration timed out', ExitCode.TIMEOUT, 'timeout')
                    stream.write(block); hasher.update(block)
        digest = hasher.hexdigest()
        if digest != manifest['source_sha256']:
            temporary.unlink(missing_ok=True)
            raise CliError('refresh source differs from the accepted install input', ExitCode.INVALID_IPA, 'refresh_source_mismatch')
        cached = sources / (digest + '.ipa')
        if cached.is_symlink():
            temporary.unlink(missing_ok=True)
            raise CliError('native source cache cannot be a symbolic link', ExitCode.PREFLIGHT, 'unsafe_source_cache')
        if cached.exists():
            if hashlib.sha256(cached.read_bytes()).hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise CliError('native source cache integrity failed', ExitCode.UNKNOWN, 'source_cache_invalid')
            temporary.unlink()
        else:
            os.rename(temporary, cached)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    job_id = hashlib.sha256((udid + '\0' + manifest['final_bundle_id']).encode()).hexdigest()[:24]
    record = {'schema': 1, 'job_id': job_id, 'device_udid': udid, 'account_selector': 'xcode:' + account_id,
              'team_id': manifest['team_id'], 'source_bundle_id': manifest['source_bundle_id'],
              'final_bundle_id': manifest['final_bundle_id'], 'source_sha256': digest,
              'strip_watch': manifest['strip_watch'], 'profiles': manifest['profiles'],
              'strip_extensions':manifest.get('strip_extensions',[]),'strict_components':manifest.get('strict_components',False),
              'component_exclusions':manifest.get('component_exclusions',[]),
              'version': manifest['version'], 'build': manifest['build'],
              'registered_at': _now().isoformat(), 'last_install_verified_at': _now().isoformat(),
              'last_renewal_verified_at': None, 'failures': 0, 'enabled': True}
    _write(records / (job_id + '.json'), record)
    return {'command': 'refresh-register', 'state': 'native_refresh_registered', 'job_id': job_id,
            'final_bundle_id': record['final_bundle_id'], 'source_copy': 'private_hash_verified',
            'scheduler': 'not_checked', 'renewal': 'not_tested',
            'message': 'native refresh record and private source snapshot created; scheduling and expiry extension are separate checks'}


def _read_record(path: Path) -> dict[str, Any]:
    import re
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise CliError('native record must be an owned regular file', ExitCode.PREFLIGHT, 'unsafe_refresh_record')
    record = json.loads(path.read_text())
    if (not isinstance(record, dict) or not re.fullmatch(r'[a-f0-9]{24}', record.get('job_id', ''))
            or path.stem != record['job_id'] or not re.fullmatch(r'[a-f0-9]{64}', record.get('source_sha256', ''))
            or not isinstance(record.get('profiles'), list) or not record['profiles']):
        raise CliError('native refresh record is invalid', ExitCode.PREFLIGHT, 'invalid_refresh_record')
    for profile in record['profiles']:
        _date(profile['expires'])
    return record


def status(state: Path) -> dict[str, Any]:
    root = _root(state)
    if (root / 'records').exists():
        _root(root / 'records')
    jobs = []
    for path in sorted((root / 'records').glob('*.json')):
        record = _read_record(path)
        jobs.append({'job_id': record['job_id'], 'final_bundle_id': record['final_bundle_id'],
                     'enabled': record.get('enabled'), 'install_requires_review':bool(record.get('pending_install')), 'expires': min(p['expires'] for p in record['profiles']),
                     'last_install_verified_at': record.get('last_install_verified_at'),
                     'last_renewal_verified_at': record.get('last_renewal_verified_at'),
                     'last_error': record.get('last_error'), 'next_attempt': record.get('next_attempt')})
    return {'command': 'refresh-status', 'state': 'native_registry_read', 'jobs': jobs,
            'message': 'native CLI registrations; this does not inspect or modify Sideloadly desktop refresh records'}


def _source_digest(path: Path, deadline: float) -> str:
    if path.is_symlink():
        raise CliError('native source cache cannot be a symbolic link',ExitCode.INVALID_IPA,'source_cache_invalid')
    try:
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while block:=stream.read(1024*1024):
                if time.monotonic()>=deadline:
                    raise CliError('native refresh timed out',ExitCode.TIMEOUT,'timeout')
                digest.update(block)
        return digest.hexdigest()
    except OSError as exc:
        raise CliError('native refresh source is unavailable',ExitCode.PREFLIGHT,'source_cache_unavailable') from exc


def run(state: Path, job_id: str | None, force: bool, timeout: float, *, account: str | None = None) -> tuple[dict[str, Any], ExitCode]:
    root = _root(state)
    deadline = time.monotonic() + timeout
    if (root / 'records').exists():
        _root(root / 'records')
    if (root / 'sources').exists():
        _root(root / 'sources')
    if account and not job_id:
        raise CliError('--account requires one explicit --job',ExitCode.USAGE,'explicit_refresh_job_required')
    if force and not job_id:
        raise CliError('--force requires one explicit --job', ExitCode.USAGE, 'explicit_refresh_job_required')
    paths = sorted((root / 'records').glob('*.json'))
    if job_id:
        paths = [p for p in paths if p.stem == job_id]
        if len(paths) != 1:
            raise CliError('native refresh job does not exist', ExitCode.USAGE, 'refresh_job_missing')
    results = []
    overall = ExitCode.OK
    for path in paths:
        try:
            record = _read_record(path)
        except (CliError,OSError,ValueError,KeyError,TypeError) as exc:
            category=exc.category if isinstance(exc,CliError) else 'invalid_refresh_record'
            results.append({'job_id':path.stem,'state':'failed','category':category})
            if overall in (ExitCode.OK,ExitCode.PARTIAL): overall=ExitCode.PREFLIGHT
            continue
        if not record.get('enabled'):
            continue
        if account:
            selected=select_account(account,record['team_id'])
            reference='xcode:'+selected
            if record['account_selector'] != reference:
                record['account_selector']=reference
                record['account_rebound_at']=_now().isoformat()
                _write(path,record)
        if record.get('pending_install') and not force:
            results.append({'job_id':record['job_id'], 'state':'install_requires_review',
                            'category':'previous_install_unresolved'})
            if overall == ExitCode.OK:
                overall = ExitCode.PARTIAL
            continue
        old_expiry = min(_date(p['expires']) for p in record['profiles'])
        if not force and (old_expiry > _now() + timedelta(hours=48) or
                          record.get('next_attempt') and _date(record['next_attempt']) > _now()):
            results.append({'job_id': record['job_id'], 'state': 'not_due'})
            continue
        source = root / 'sources' / (record['source_sha256'] + '.ipa')
        try:
            if _source_digest(source,deadline) != record['source_sha256']:
                raise CliError('native refresh source integrity failed', ExitCode.INVALID_IPA, 'source_cache_invalid')
            with tempfile.TemporaryDirectory(prefix='sideloadly-refresh-', dir='/private/tmp') as tmp:
                prepared = Path(tmp) / 'prepared'
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CliError('native refresh timed out', ExitCode.TIMEOUT, 'timeout')
                mode = 'exact' if record['source_bundle_id'] == record['final_bundle_id'] else 'automatic'
                manifest = prepare(source, prepared, record['account_selector'], record['team_id'],
                                   record['source_bundle_id'], mode, record['device_udid'],
                                   record['strip_watch'], remaining, renew_profiles=True,strip_extensions=record.get('strip_extensions',[]),strict_components=record.get('strict_components',False),component_exclusions=record.get('component_exclusions',[]))
                old_profiles = {p['bundle_id']: _date(p['expires']) for p in record['profiles']}
                new_profiles = {p['bundle_id']: _date(p['expires']) for p in manifest['profiles']}
                if (old_profiles.keys() != new_profiles.keys() or
                        len(new_profiles) != len(manifest['profiles'])):
                    raise CliError('refresh must preserve every registered executable bundle',
                                   ExitCode.UNKNOWN, 'refresh_component_mismatch')
                def install_started():
                    record['pending_install'] = {'started_at':_now().isoformat(),
                                                 'profiles':manifest['profiles']}
                    _write(path,record)
                result = install_prepared(prepared, record['device_udid'], record['team_id'],
                                          record['final_bundle_id'], max(0.001, deadline - time.monotonic()),
                                          on_install_started=install_started)
                if result['device_app'] != 'version_and_build_verified':
                    raise CliError('refresh reinstall could not be verified on the target',
                                   ExitCode(result.get('exit_code',ExitCode.PARTIAL)),
                                   result.get('category','refresh_install_unverified'))
                new_expiry = min(_date(p['expires']) for p in manifest['profiles'])
                # A newer main-app profile alone cannot certify that its
                # extension was renewed too, or silently dropped.
                extended = (old_profiles.keys() == new_profiles.keys() and
                            all(new_profiles[bundle] > expiry for bundle, expiry in old_profiles.items()))
                record.pop('pending_install',None)
                record['profiles'] = manifest['profiles']
                record['last_install_verified_at'] = _now().isoformat()
                record['failures'] = 0
                record.pop('last_error', None); record.pop('next_attempt', None)
                if extended:
                    record['last_renewal_verified_at'] = _now().isoformat()
                else:
                    record['next_attempt'] = (_now() + timedelta(hours=6)).isoformat()
                _write(path, record)
                results.append({'job_id': record['job_id'], 'state': 'renewed_and_installed' if extended else 'reinstalled_without_expiry_extension',
                                'old_expiry': old_expiry.isoformat(), 'new_expiry': new_expiry.isoformat(),
                                'expiry_extended': extended, 'device_app': 'version_and_build_verified'})
                if not extended and overall == ExitCode.OK:
                    overall = ExitCode.PARTIAL
        except CliError as exc:
            record['failures'] = record.get('failures', 0) + 1
            record['last_error'] = exc.category
            record['next_attempt'] = (_now() + timedelta(hours=min(24, 2 ** record['failures']))).isoformat()
            _write(path, record)
            results.append({'job_id': record['job_id'],
                            'state': 'install_requires_review' if record.get('pending_install') else 'failed',
                            'category': exc.category, 'message':exc.message})
            if overall in (ExitCode.OK,ExitCode.PARTIAL):
                overall = exc.code
    return {'command': 'refresh-native', 'state': 'native_refresh_checked', 'jobs': results,
            'message': 'expiry extension and device reinstall were checked independently for each attempted native job'}, overall
