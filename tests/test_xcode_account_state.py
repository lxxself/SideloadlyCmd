import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sideloadlycmd import xcode_account_state as state
from sideloadlycmd.errors import CliError


class XcodeAccountReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.home=patch.object(state.Path,'home',return_value=self.root);self.home.start();self.addCleanup(self.home.stop)
        self.before={'group':[{'identifier':'saved-account','username':'user@example.invalid'}]}

    def test_sdk_clearing_references_restores_snapshot_and_stops_operation(self):
        with patch.object(state,'_preference',side_effect=[self.before,{},None,self.before]) as preference:
            with self.assertRaises(CliError) as caught:
                with state.preserve_account_references(): pass
        self.assertEqual(caught.exception.category,'xcode_account_context_lost')
        self.assertEqual(preference.call_args_list[2].kwargs,{'write':True})
        self.assertEqual(preference.call_args_list[2].args[0],self.before)
        saved=next(self.root.rglob('account-references.json'))
        self.assertEqual(saved.stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads(saved.read_text())['account_references'],self.before)

    def test_unchanged_accounts_do_not_write_preferences(self):
        with patch.object(state,'_preference',side_effect=[self.before,self.before]) as preference:
            with state.preserve_account_references(): pass
        self.assertFalse(any(c.kwargs.get('write') for c in preference.call_args_list))

    def test_user_replacing_account_is_preserved_and_operation_stops(self):
        with patch.object(state,'_preference',side_effect=[self.before,{'group':[{'identifier':'different-account'}]}]) as preference:
            with self.assertRaises(CliError) as caught:
                with state.preserve_account_references(): pass
        self.assertEqual(caught.exception.category,'xcode_account_context_changed')
        self.assertFalse(any(c.kwargs.get('write') for c in preference.call_args_list))

    def test_credential_fields_are_never_written_to_plaintext_backup(self):
        with patch.object(state,'_preference',return_value={'group':[{'identifier':'saved-account','token':'must-not-save'}]}):
            with self.assertRaises(CliError):
                with state.preserve_account_references(): self.fail('build must not start')
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_build_failure_is_preserved_when_references_survive(self):
        with patch.object(state,'_preference',side_effect=[self.before,self.before]):
            with self.assertRaisesRegex(RuntimeError,'build failed'):
                with state.preserve_account_references(): raise RuntimeError('build failed')

    def test_native_error_diagnostics_never_echo_credentials(self):
        import subprocess
        from sideloadlycmd.xcode_backend import _provisioning_diagnostics
        result=subprocess.CompletedProcess([],65,b'',b'owner@example.invalid token=private-value missing Xcode-Token DVTDeveloperAccountCredentialsError')
        diagnostics=_provisioning_diagnostics(result)
        self.assertIn('missing_account_token',diagnostics)
        self.assertNotIn('private-value',repr(diagnostics))
        self.assertNotIn('owner@example.invalid',repr(diagnostics))
