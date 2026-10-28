import os
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from sideloadlycmd import native_doctor, scheduler

ROOT=Path(__file__).resolve().parents[1]

class PortableRuntimeTests(unittest.TestCase):
    def test_install_isolated_and_refuses_foreign_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix=Path(temp)/'runtime with spaces'
            bindir=Path(temp)/'bin'
            command=[sys.executable,str(ROOT/'scripts/install_macos.py'),'--prefix',str(prefix),'--bin-dir',str(bindir)]
            subprocess.run(command,check=True,capture_output=True)
            result=subprocess.run([str(bindir/'sideloadlycmd'),'native-doctor','--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--offline',result.stdout)
            subprocess.run(command,check=True,capture_output=True)
            (bindir/'sideloadlycmd').write_text('foreign launcher')
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            self.assertEqual((bindir/'sideloadlycmd').read_text(),'foreign launcher')
    def test_scheduler_has_both_homebrew_paths_and_fallback(self):
        result=scheduler.definition(Path('/tmp/state'),Path('/missing/entry'))
        self.assertIn('/usr/local/bin',result['EnvironmentVariables']['PATH'].split(':'))
        self.assertIn('/opt/homebrew/bin',result['EnvironmentVariables']['PATH'].split(':'))
        self.assertTrue(Path(result['ProgramArguments'][1]).is_file())
    def test_doctor_non_macos_never_calls_tools(self):
        with patch.object(native_doctor.platform,'system',return_value='Linux'), patch.object(native_doctor,'_run') as run:
            result,code=native_doctor.diagnose()
            self.assertNotEqual(int(code),0)
            run.assert_not_called()
            self.assertEqual(result['state'],'native_setup_required')

    def test_upgrade_restores_runtime_if_staging_rename_fails(self):
        spec=importlib.util.spec_from_file_location('runtime_installer',ROOT/'scripts/install_macos.py')
        installer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as temp:
            prefix=Path(temp)/'runtime'
            bindir=Path(temp)/'bin'
            argv=['installer','--prefix',str(prefix),'--bin-dir',str(bindir)]
            with patch.object(sys,'argv',argv), contextlib.redirect_stdout(io.StringIO()): installer.main()
            (prefix/'old-sentinel').write_text('preserve')
            original=(bindir/'sideloadlycmd').read_bytes()
            rename=Path.rename
            def fail_staging(path,target):
                if path.name.startswith('.sideloadlycmd-install-'): raise OSError('simulated rename failure')
                return rename(path,target)
            with patch.object(sys,'argv',argv), patch.object(Path,'rename',fail_staging):
                with self.assertRaises(OSError): installer.main()
            self.assertEqual((prefix/'old-sentinel').read_text(),'preserve')
            self.assertEqual((bindir/'sideloadlycmd').read_bytes(),original)
            self.assertFalse(list(bindir.glob('.sideloadlycmd-*')))
            self.assertFalse(list(prefix.parent.glob('.sideloadlycmd-install-*')))

    def test_manifest_tamper_stops_before_install(self):
        with tempfile.TemporaryDirectory() as temp:
            kit=Path(temp)/'kit'
            (kit/'scripts').mkdir(parents=True)
            import shutil
            shutil.copy2(ROOT/'scripts/install_macos.py',kit/'scripts/install_macos.py')
            (kit/'MANIFEST.json').write_text(json.dumps({'format':1,'sha256':{'scripts/install_macos.py':'0'*64}}))
            result=subprocess.run([sys.executable,str(kit/'scripts/install_macos.py'),'--prefix',str(Path(temp)/'runtime'),'--bin-dir',str(Path(temp)/'bin')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('integrity mismatch',result.stderr)
            self.assertFalse((Path(temp)/'runtime').exists())
