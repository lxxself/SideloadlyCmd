from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sideloadlycmd import refresh
from sideloadlycmd.cli import _redact
from sideloadlycmd.errors import CliError, ExitCode
from sideloadlycmd.scheduler import definition
from sideloadlycmd.xcode_backend import _run


class NativeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'records').mkdir()
        (self.root / 'sources').mkdir()
        digest = hashlib.sha256(b'accepted-source').hexdigest()
        (self.root / 'sources' / (digest + '.ipa')).write_bytes(b'accepted-source')
        self.old = datetime.now(timezone.utc) + timedelta(days=1)
        self.record = {'job_id':'a' * 24, 'enabled':True, 'profiles':[{'bundle_id':'com.example.TEAM','expires':self.old.isoformat()}],
                       'source_sha256':digest, 'source_bundle_id':'com.example', 'final_bundle_id':'com.example.TEAM',
                       'account_selector':'xcode:private-id', 'team_id':'TEAM', 'device_udid':'private-device',
                       'strip_watch':True, 'failures':0}
        self.path = self.root / 'records' / ('a' * 24 + '.json')
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.record))

    def test_reinstall_is_not_renewal_without_expiry_extension(self):
        with patch.object(refresh, 'prepare', return_value={'profiles':self.record['profiles']}), \
             patch.object(refresh, 'install_prepared', return_value={'device_app':'version_and_build_verified'}):
            result, code = refresh.run(self.root, self.record['job_id'], True, 30)
        self.assertEqual(code, ExitCode.PARTIAL)
        self.assertFalse(result['jobs'][0]['expiry_extended'])
        self.assertNotIn('last_renewal_verified_at', json.loads(self.path.read_text()))

    def test_earliest_extension_profile_must_extend_too(self):
        self.record['profiles'].append({'bundle_id':'com.example.TEAM.widget','expires':self.old.isoformat()})
        self.save()
        profiles = [{'bundle_id':'com.example.TEAM','expires':(self.old + timedelta(days=1)).isoformat()}, {'bundle_id':'com.example.TEAM.widget','expires':self.old.isoformat()}]
        with patch.object(refresh, 'prepare', return_value={'profiles':profiles}), \
             patch.object(refresh, 'install_prepared', return_value={'device_app':'version_and_build_verified'}):
            result, code = refresh.run(self.root, self.record['job_id'], True, 30)
        self.assertEqual(code, ExitCode.PARTIAL)
        self.assertFalse(result['jobs'][0]['expiry_extended'])

    def test_extended_profile_and_verified_install_records_renewal(self):
        profiles = [{'bundle_id':'com.example.TEAM','expires':(self.old + timedelta(days=1)).isoformat()}]
        with patch.object(refresh, 'prepare', return_value={'profiles':profiles}), \
             patch.object(refresh, 'install_prepared', return_value={'device_app':'version_and_build_verified'}):
            result, code = refresh.run(self.root, self.record['job_id'], True, 30)
        self.assertEqual(code, ExitCode.OK)
        self.assertTrue(result['jobs'][0]['expiry_extended'])
        self.assertTrue(json.loads(self.path.read_text())['last_renewal_verified_at'])

    def test_missing_extension_is_rejected_before_installation(self):
        self.record['profiles'].append({'bundle_id':'com.example.TEAM.widget','expires':self.old.isoformat()})
        self.save()
        profiles = [{'bundle_id':'com.example.TEAM','expires':(self.old + timedelta(days=1)).isoformat()}]
        with patch.object(refresh, 'prepare', return_value={'profiles':profiles}), \
             patch.object(refresh, 'install_prepared') as install:
            result, code = refresh.run(self.root, self.record['job_id'], True, 30)
        install.assert_not_called()
        self.assertEqual(code, ExitCode.UNKNOWN)
        self.assertEqual(result['jobs'][0]['category'], 'refresh_component_mismatch')
        self.assertEqual(len(json.loads(self.path.read_text())['profiles']), 2)

    def test_failure_saves_retry_backoff_and_does_not_mark_renewed(self):
        with patch.object(refresh, 'prepare', side_effect=CliError('offline', ExitCode.PREFLIGHT, 'device_offline')):
            result, code = refresh.run(self.root, self.record['job_id'], True, 30)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['failures'], 1)
        self.assertGreater(refresh._date(saved['next_attempt']), datetime.now(timezone.utc))
        self.assertEqual(result['jobs'][0]['state'], 'failed')
        self.assertNotIn('last_renewal_verified_at', saved)

    def test_force_requires_one_explicit_target(self):
        with self.assertRaises(CliError): refresh.run(self.root, None, True, 30)

    def test_scheduled_not_due_does_not_sign_or_install(self):
        self.record['profiles'][0]['expires'] = (self.old + timedelta(days=3)).isoformat()
        self.save()
        with patch.object(refresh, 'prepare') as prepare:
            result, code = refresh.run(self.root, None, False, 30)
        prepare.assert_not_called()
        self.assertEqual(result['jobs'][0]['state'], 'not_due')

    def test_interrupted_install_blocks_automatic_retry(self):
        def interrupted(*args, **kwargs):
            kwargs['on_install_started']()
            raise CliError('interrupted',ExitCode.TIMEOUT,'timeout')
        with patch.object(refresh,'prepare',return_value={'profiles':self.record['profiles']}), \
             patch.object(refresh,'install_prepared',side_effect=interrupted):
            result, code = refresh.run(self.root,self.record['job_id'],True,30)
        self.assertEqual(result['jobs'][0]['state'],'install_requires_review')
        self.assertTrue(json.loads(self.path.read_text())['pending_install'])
        with patch.object(refresh,'prepare') as prepare, patch.object(refresh,'install_prepared') as install:
            result, code = refresh.run(self.root,None,False,30)
        prepare.assert_not_called(); install.assert_not_called()
        self.assertEqual(code,ExitCode.PARTIAL)
        self.assertEqual(result['jobs'][0]['category'],'previous_install_unresolved')

    def test_account_rebind_requires_one_explicit_job(self):
        with patch.object(refresh,'select_account') as select:
            with self.assertRaises(CliError): refresh.run(self.root,None,False,30,account='xcode')
        select.assert_not_called()

    def test_explicit_rebind_preserves_profile_history(self):
        self.record['profiles'][0]['expires']=(self.old+timedelta(days=3)).isoformat();self.save()
        before=json.loads(self.path.read_text())['profiles']
        with patch.object(refresh,'select_account',return_value='new-local-reference'):
            result,code=refresh.run(self.root,self.record['job_id'],False,30,account='xcode')
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved['account_selector'],'xcode:new-local-reference')
        self.assertEqual(saved['profiles'],before)
        self.assertEqual(result['jobs'][0]['state'],'not_due')
        self.assertIn('account_rebound_at',saved)

    def second_record(self):
        second=dict(self.record);second['job_id']='b'*24
        path=self.root/'records'/('b'*24+'.json');path.write_text(json.dumps(second))
        return second,path

    def test_batch_partial_failure_cannot_hide_earlier_fatal_error(self):
        self.second_record()
        with patch.object(refresh,'prepare',side_effect=[CliError('failed',ExitCode.INSTALL_FAILED,'signing_failed'),{'profiles':self.record['profiles']}]), \
             patch.object(refresh,'install_prepared',return_value={'device_app':'unverified'}):
            result,code=refresh.run(self.root,None,False,30)
        self.assertEqual(code,ExitCode.INSTALL_FAILED)
        self.assertEqual(len(result['jobs']),2)

    def test_later_missing_source_preserves_earlier_renewal_result(self):
        second,path=self.second_record();second['source_sha256']='f'*64;path.write_text(json.dumps(second))
        profiles=[{'bundle_id':'com.example.TEAM','expires':(self.old+timedelta(days=1)).isoformat()}]
        with patch.object(refresh,'prepare',return_value={'profiles':profiles}), \
             patch.object(refresh,'install_prepared',return_value={'device_app':'version_and_build_verified'}):
            result,code=refresh.run(self.root,None,False,30)
        self.assertEqual(code,ExitCode.PREFLIGHT)
        self.assertEqual(result['jobs'][0]['state'],'renewed_and_installed')
        self.assertEqual(result['jobs'][1]['category'],'source_cache_unavailable')

    def test_later_corrupt_record_does_not_erase_previous_job_result(self):
        second,path=self.second_record();path.write_text('invalid-json')
        self.record['profiles'][0]['expires']=(self.old+timedelta(days=3)).isoformat();self.save()
        result,code=refresh.run(self.root,None,False,30)
        self.assertEqual(code,ExitCode.PREFLIGHT)
        self.assertEqual(result['jobs'][0]['state'],'not_due')
        self.assertEqual(result['jobs'][1]['category'],'invalid_refresh_record')

    def test_public_job_reference_is_usable_but_device_stays_private(self):
        result = _redact({'job_id':'a' * 24, 'device':'a' * 24, 'account':'private'})
        self.assertEqual(result['job_id'], 'a' * 24)
        self.assertNotEqual(result['device'], 'a' * 24)
        self.assertEqual(result['account'], '<redacted>')

    def test_scheduler_never_forces_batch_or_runs_at_load(self):
        data = definition(self.root, Path(__file__))
        self.assertFalse(data['RunAtLoad'])
        self.assertNotIn('--force', data['ProgramArguments'])
        self.assertEqual(data['Umask'], 0o077)

    def test_timeout_cleans_descendants_after_parent_exit(self):
        import sys
        started = time.monotonic()
        with self.assertRaises(CliError) as caught:
            _run([sys.executable, '-c', 'import subprocess; subprocess.Popen(["/bin/sleep", "20"])'], started + .2)
        self.assertEqual(caught.exception.code, ExitCode.TIMEOUT)
        self.assertLess(time.monotonic() - started, 3)
