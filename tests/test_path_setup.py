import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('install_path_setup',Path(__file__).parents[1]/'scripts/install_macos.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class PathSetupTests(unittest.TestCase):
    def test_idempotent_and_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);(home/'.zshrc').write_text('export EXISTING=1\n')
            bindir=home/'bin with spaces'
            installer.apply_path(installer.plan_path(home,bindir,'zsh'))
            first=(home/'.zshrc').read_text()
            installer.apply_path(installer.plan_path(home,bindir,'zsh'))
            self.assertEqual((home/'.zshrc').read_text(),first)
            self.assertTrue(first.startswith('export EXISTING=1\n'))
            script='source "$1"; source "$2"; print -r -- "$PATH"'
            result=subprocess.run(['/bin/zsh','-f','-c',script,'zsh',str(home/'.zprofile'),str(home/'.zshrc')],capture_output=True,text=True,env={**os.environ,'PATH':'/usr/bin:/bin'},check=True)
            self.assertEqual(result.stdout.strip().split(':').count(str(bindir)),1)
    def test_refuses_symlink_and_malformed_managed_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);target=home/'elsewhere';target.write_text('keep')
            (home/'.zshrc').symlink_to(target)
            with self.assertRaises(ValueError): installer.plan_path(home,home/'bin','zsh')
            self.assertEqual(target.read_text(),'keep')
            (home/'.zshrc').unlink();(home/'.zshrc').write_text(installer.START)
            with self.assertRaises(ValueError): installer.plan_path(home,home/'bin','zsh')
    def test_second_profile_failure_restores_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);(home/'.zprofile').write_text('original\n')
            original=installer.write_profile
            def failing(path,text,mode):
                if path.name=='.zshrc': raise OSError('fixture')
                return original(path,text,mode)
            with patch.object(installer,'write_profile',side_effect=failing):
                with self.assertRaises(OSError): installer.apply_path(installer.plan_path(home,home/'bin','zsh'))
            self.assertEqual((home/'.zprofile').read_text(),'original\n')
