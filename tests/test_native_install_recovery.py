from __future__ import annotations
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from sideloadlycmd import xcode_backend
from sideloadlycmd.errors import CliError, ExitCode
from sideloadlycmd.private_state import write_json


class NativeInstallRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        app = self.root/'Fixture.app'; app.mkdir()
        metadata = {'CFBundleIdentifier':'com.example.app', 'CFBundleShortVersionString':'1.0','CFBundleVersion':'1'}
        (app/'Info.plist').write_bytes(plistlib.dumps(metadata))
        (app/'embedded.mobileprovision').write_bytes(b'fixture')
        self.manifest = {'app_directory':'Fixture.app', 'team_id':'TESTTEAM01','final_bundle_id':'com.example.app',
                         'version':'1.0','build':'1','device_sha256':hashlib.sha256(b'target').hexdigest()}
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        self.install_calls = 0
        self.query_calls = 0
        self.mode = 'query_timeout'

    def backend(self, command, deadline, **kwargs):
        if command[0] == '/usr/bin/codesign':
            prefix = next((part.partition('=')[2] for part in command if part.startswith('--extract-certificates=')),None)
            if prefix: Path(prefix+'0').write_bytes(b'certificate')
            return subprocess.CompletedProcess(command,0,b'',b'')
        if 'install' in command:
            self.install_calls += 1
            receipt = json.loads((self.root/'install-receipt.json').read_text())
            self.assertEqual(receipt['state'],'install_completion_unknown')
            if self.mode == 'install_timeout':
                raise CliError('deadline',ExitCode.TIMEOUT,'timeout')
            output = Path(command[command.index('--json-output')+1]); output.write_text('{"result":{}}')
            return subprocess.CompletedProcess(command,0,b'',b'')
        self.query_calls += 1
        if self.mode == 'query_timeout': raise CliError('deadline',ExitCode.TIMEOUT,'timeout')
        output = Path(command[command.index('--json-output')+1])
        if self.mode == 'coredevice_mismatch':
            output.write_text(json.dumps({'result':{'apps':[{'bundleIdentifier':'com.example.app','version':'wrong','bundleVersion':'1'}]}}))
        else: output.write_text('{"result":[]}')
        return subprocess.CompletedProcess(command,0,b'',b'')

    def invoke(self, **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(xcode_backend,'resolve_device',return_value='target'))
            stack.enter_context(patch.object(xcode_backend,'_identity_hashes',return_value={'fingerprint'}))
            stack.enter_context(patch.object(xcode_backend,'_decode_profile',return_value={'DeveloperCertificates':[b'certificate']}))
            stack.enter_context(patch.object(xcode_backend,'_profile_usable',return_value='fingerprint'))
            stack.enter_context(patch.object(xcode_backend,'_run',side_effect=self.backend))
            return xcode_backend.install_prepared(self.root,'target','TESTTEAM01','com.example.app',30,**kwargs)

    def test_query_timeout_preserves_install_completion_and_does_not_reinstall(self):
        result = self.invoke()
        self.assertEqual(result['install'],'completed_this_run')
        self.assertEqual(result['device_app'],'unverified')
        self.assertEqual(self.install_calls,1)
        self.assertEqual(json.loads((self.root/'install-receipt.json').read_text())['install'],'completed_this_run')

    def test_install_timeout_is_unknown_not_a_claim_of_failure_or_success(self):
        self.mode = 'install_timeout'
        result = self.invoke()
        self.assertEqual(result['state'],'install_completion_unknown')
        self.assertEqual(result['exit_code'],31)
        self.assertEqual(self.install_calls,1)
        self.assertEqual(self.query_calls,0)

    def test_malformed_post_install_reply_keeps_fresh_completion(self):
        self.mode = 'malformed_query'
        result = self.invoke()
        self.assertEqual(result['install'],'completed_this_run')
        self.assertEqual(result['device_app'],'unverified')

    def test_state_write_failure_stops_before_device_mutation(self):
        with patch.object(xcode_backend,'write_json',side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError): self.invoke()
        self.assertEqual(self.install_calls,0)

    def test_refresh_journal_callback_runs_before_device_mutation(self):
        def started():
            self.assertEqual(self.install_calls,0)
            raise OSError('cannot journal')
        with self.assertRaises(OSError): self.invoke(on_install_started=started)
        self.assertEqual(self.install_calls,0)

    def test_failed_json_serialization_preserves_old_state_and_cleans_temporary(self):
        path = self.root/'state.json'; path.write_text('{"old":true}')
        with self.assertRaises(TypeError): write_json(path,{'bad':object()})
        self.assertEqual(json.loads(path.read_text()),{'old':True})
        self.assertEqual(list(self.root.glob('.state-*')),[])

    def test_fallback_confirms_exact_bundle_and_version_after_completed_install(self):
        row={'bundleIdentifier':'com.example.app','version':'1.0','bundleVersion':'1'}
        with patch.object(xcode_backend,'_lookup_installed_app',return_value=row): result=self.invoke()
        self.assertEqual(result['device_app'],'version_and_build_verified')
        self.assertEqual(result['verification_engine'],'libimobiledevice_installation_proxy')
        self.assertEqual(self.install_calls,1)

    def test_coredevice_explicit_version_mismatch_cannot_be_overridden_by_fallback(self):
        self.mode='coredevice_mismatch'
        with patch.object(xcode_backend,'_lookup_installed_app') as alternative: result=self.invoke()
        alternative.assert_not_called()
        self.assertEqual(result['device_app'],'unverified')

    def test_readonly_verification_requires_prior_completion_and_never_installs(self):
        receipt={'install':'completed_this_run','final_bundle_id':'com.example.app','team_id':'TESTTEAM01','version':'1.0','build':'1','installation_result_present':True}
        (self.root/'install-receipt.json').write_text(json.dumps(receipt))
        row={'bundleIdentifier':'com.example.app','version':'1.0','bundleVersion':'1'}
        with patch.object(xcode_backend,'_lookup_installed_app',return_value=row): result=self.invoke(verify_only=True)
        self.assertEqual(self.install_calls,0)
        self.assertEqual(result['command'],'verify-prepared')
        self.assertEqual(result['install'],'completed_prior_attempt')
        self.assertEqual(result['device_app'],'version_and_build_verified')

    def test_readonly_verification_cannot_invent_install_receipt(self):
        with self.assertRaises(CliError) as caught: self.invoke(verify_only=True)
        self.assertEqual(caught.exception.category,'install_receipt_missing')
        self.assertEqual(self.install_calls,0)

    def test_fallback_wrong_build_remains_unverified(self):
        with patch.object(xcode_backend,'_lookup_installed_app',return_value={'bundleIdentifier':'com.example.app','version':'1.0','bundleVersion':'wrong'}):
            result=self.invoke()
        self.assertEqual(result['device_app'],'unverified')
