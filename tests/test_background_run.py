import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from sideloadlycmd import background_run as bg
from sideloadlycmd.errors import CliError,ExitCode

class BackgroundRunTests(unittest.TestCase):
    def test_rejects_gui_login_and_recursive_commands_before_launch(self):
        for command in ['login','install','background-run','refresh-scheduler']:
            with self.subTest(command=command),patch.object(bg,'_run') as native:
                with self.assertRaises(CliError): bg.run([command],30)
                native.assert_not_called()
    def test_missing_selectors_are_rejected_before_launch(self):
        with contextlib.redirect_stderr(io.StringIO()),patch.object(bg,'_run') as native:
            with self.assertRaises(SystemExit): bg.run(['prepare','app.ipa'],30)
            native.assert_not_called()
    def test_worker_preserves_partial_exit_and_discards_unstructured_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); config=root/'config.json'
            config.write_text(json.dumps({'arguments':['refresh-status','--json'],'deadline':time.monotonic()+30}))
            response={'state':'fixture_partial','message':'fixture'}
            def native_main(args):
                print(json.dumps(response))
                print('not persisted secret-fixture',file=__import__('sys').stderr)
                return 3
            with patch('sideloadlycmd.background_keychain._security'),patch('sideloadlycmd.background_keychain._status',return_value={'graphic_access':False,'root_security_session':False,'unlocked':True}),patch('sideloadlycmd.cli.main',side_effect=native_main):
                bg.worker(config)
            result=json.loads((root/'result.json').read_text())
            self.assertEqual(result['exit_code'],3)
            self.assertEqual(result['result'],response)
            self.assertNotIn('secret-fixture',(root/'result.json').read_text())
    def test_worker_refuses_locked_keychain_before_signing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps({'arguments':['prepare'],'deadline':time.monotonic()+30}))
            with patch('sideloadlycmd.background_keychain._security'),patch('sideloadlycmd.background_keychain._status',return_value={'graphic_access':False,'root_security_session':False,'unlocked':False}),patch.object(bg,'_run') as native:
                bg.worker(config)
            result=json.loads((Path(tmp)/'result.json').read_text())
            self.assertEqual(result['exit_code'],32)
            self.assertEqual(result['category'],'background_keychain_locked')
            native.assert_not_called()
    def test_worker_propagates_timeout_without_exposing_raw_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps({'arguments':['refresh-status'],'deadline':time.monotonic()+30}))
            with patch('sideloadlycmd.background_keychain._security'),patch('sideloadlycmd.background_keychain._status',return_value={'graphic_access':False,'root_security_session':False,'unlocked':True}),patch('sideloadlycmd.cli.main',side_effect=CliError('deadline expired',ExitCode.TIMEOUT,'timeout')):
                bg.worker(config)
            self.assertEqual(json.loads((Path(tmp)/'result.json').read_text())['exit_code'],31)

    def test_bootstrap_timeout_still_attempts_removal_of_unique_job(self):
        with patch.object(bg.sys,'platform','darwin'),patch.object(bg,'_run',side_effect=CliError('deadline',ExitCode.TIMEOUT,'timeout')),patch.object(bg.subprocess,'run',return_value=subprocess.CompletedProcess([],113)) as cleanup:
            with self.assertRaises(CliError): bg.run(['refresh-status'],30)
        self.assertTrue(any(call.args[0][1]=='bootout' for call in cleanup.call_args_list))

    def test_native_timeout_is_clamped_inside_public_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps({'arguments':['native-doctor','--timeout','300','--json'],'deadline':time.monotonic()+5}))
            def native_main(args):
                self.assertLessEqual(float(args[-1]),5)
                self.assertEqual(args[-2],'--timeout')
                print(json.dumps({'state':'fixture_done'}))
                return 0
            with patch('sideloadlycmd.background_keychain._security'),patch('sideloadlycmd.background_keychain._status',return_value={'graphic_access':False,'root_security_session':False,'unlocked':True}),patch('sideloadlycmd.cli.main',side_effect=native_main):
                bg.worker(config)
            self.assertEqual(json.loads((Path(tmp)/'result.json').read_text())['exit_code'],0)
