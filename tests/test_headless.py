from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sideloadlycmd.errors import CliError, ExitCode
from sideloadlycmd.headless import doctor, login, run_backend


class HeadlessBoundaryTests(unittest.TestCase):
    def test_login_without_terminal_does_not_start_backend(self):
        with patch('sideloadlycmd.headless.validate_backend', return_value=Path('/fake')), \
             patch('sideloadlycmd.headless.sys.stdin.isatty', return_value=False), \
             patch('sideloadlycmd.headless.subprocess.Popen') as spawn:
            with self.assertRaises(CliError) as raised:
                login(Path('/fake'), Path('/unused'), 1)
        self.assertEqual(raised.exception.code, ExitCode.NEEDS_USER)
        spawn.assert_not_called()

    def test_preflight_requires_fresh_explicit_result(self):
        for output in ('{}', '{"state":"native_headers_available"}', 'not json'):
            with self.subTest(output=output), \
                 patch('sideloadlycmd.headless.validate_backend', return_value=Path('/fake')), \
                 patch('sideloadlycmd.headless.run_backend', return_value=subprocess.CompletedProcess([], 1, output, 'secret')):
                with self.assertRaises(CliError) as raised:
                    doctor(Path('/fake'), 1)
                self.assertNotIn('secret', str(raised.exception))

    def test_timeout_kills_descendants_even_after_backend_parent_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / 'backend'
            # Parent exits, but the descendant keeps stdout/stderr open.
            backend.write_text('#!' + sys.executable + '\nimport subprocess,sys\nsubprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"])\n')
            backend.chmod(0o700)
            with self.assertRaises(CliError) as raised:
                run_backend(backend, [], 0.15)
        self.assertEqual(raised.exception.code, ExitCode.TIMEOUT)

    def test_private_state_and_environment_without_injected_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / 'backend'
            backend.write_text('#!' + sys.executable + '\nimport json,os\nprint(json.dumps({"inject":os.environ.get("DYLD_INSERT_LIBRARIES"),"config":os.environ.get("SIDELOADER_CONFIG_DIR"),"mask":os.umask(0)}))\n')
            backend.chmod(0o700)
            state = Path(tmp) / 'state'
            with patch.dict('os.environ', {'DYLD_INSERT_LIBRARIES': '/unwanted'}):
                result = run_backend(backend, [], 10, state_dir=state)
            row = json.loads(result.stdout)
            self.assertIsNone(row['inject'])
            self.assertEqual(row['mask'], 0o077)
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)

    def test_state_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            state.symlink_to(tmp)
            with patch('sideloadlycmd.headless.validate_backend', return_value=Path('/fake')):
                with self.assertRaises(CliError):
                    run_backend(Path('/fake'), [], 1, state_dir=state)

class AutomaticDeviceTests(unittest.TestCase):
    def resolve(self,ids,selector=None):
        import time
        from sideloadlycmd.headless import resolve_device
        with patch('shutil.which',side_effect=lambda name:'/tools/'+name),patch('sideloadlycmd.headless.subprocess.run',return_value=subprocess.CompletedProcess([],0,ids,'')) as query:
            result=resolve_device(selector,time.monotonic()+10)
        return result,query
    def test_single_usb_is_selected_without_name_queries(self):
        result,query=self.resolve('selected-device\n')
        self.assertEqual(result,'selected-device')
        self.assertEqual(query.call_count,1)
    def test_no_usb_device_is_an_explicit_error(self):
        with self.assertRaises(CliError) as raised:self.resolve('')
        self.assertEqual(raised.exception.category,'device_not_found')
    def test_multiple_usb_devices_require_explicit_selection(self):
        with self.assertRaises(CliError) as raised:self.resolve('first\nsecond\n')
        self.assertEqual(raised.exception.category,'device_selection_required')
    def test_explicit_id_is_honored_even_when_multiple_connected(self):
        self.assertEqual(self.resolve('first\nsecond\n','second')[0],'second')
