from pathlib import Path
import os
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sideloadlycmd.errors import CliError
from sideloadlycmd.scheduler import LABEL, definition, manage


def completed(code, path=None):
    return subprocess.CompletedProcess([],code,('path = '+str(path)+'\n').encode() if path else b'', b'')


class NativeSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home/'state'
        self.launcher = Path(__file__)
        self.persistent = self.home/'Library/LaunchAgents'/(LABEL+'.plist')
        self.home_patch = patch('sideloadlycmd.scheduler.Path.home',return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def test_persistent_enable_bootstraps_background_config_with_private_permissions(self):
        with patch('sideloadlycmd.scheduler._run',side_effect=[completed(1),completed(0),completed(0,self.persistent)]) as run:
            result = manage('enable',self.root,self.launcher,30,persist=True)
        self.assertTrue(result['loaded'])
        self.assertEqual(result['persistence'],'launchagent_configured_reboot_unverified')
        self.assertEqual(self.persistent.stat().st_mode & 0o777,0o600)
        data = plistlib.loads(self.persistent.read_bytes())
        self.assertEqual(data['LimitLoadToSessionType'],'Background')
        self.assertEqual(run.call_args_list[1].args[0],['/bin/launchctl','bootstrap','user/'+str(os.getuid()),str(self.persistent)])

    def test_cannot_disable_loaded_service_from_another_state(self):
        with patch('sideloadlycmd.scheduler._run',return_value=completed(0,self.home/'other/native-refresh.plist')) as run:
            with self.assertRaises(CliError) as caught:
                manage('disable',self.root,self.launcher,30)
        self.assertEqual(caught.exception.category,'scheduler_state_mismatch')
        self.assertEqual(run.call_count,1)

    def test_does_not_overwrite_foreign_persistent_configuration(self):
        self.persistent.parent.mkdir(parents=True)
        content = plistlib.dumps({'Label':LABEL,'ProgramArguments':['/foreign']})
        self.persistent.write_bytes(content)
        with patch('sideloadlycmd.scheduler._run',return_value=completed(1)):
            with self.assertRaises(CliError): manage('enable',self.root,self.launcher,30,persist=True)
        self.assertEqual(self.persistent.read_bytes(),content)

    def test_disable_verifies_unloaded_before_removing_persistent_config(self):
        self.persistent.parent.mkdir(parents=True)
        self.persistent.write_bytes(plistlib.dumps(definition(self.root,self.launcher)))
        with patch('sideloadlycmd.scheduler._run',side_effect=[completed(0,self.persistent),completed(0),completed(1)]):
            result = manage('disable',self.root,self.launcher,30)
        self.assertFalse(result['loaded'])
        self.assertFalse(self.persistent.exists())

    def test_explicit_xcode_selection_survives_background_launch(self):
        with patch.dict(os.environ,{'DEVELOPER_DIR':'/Applications/Xcode.app/Contents/Developer'}):
            data=definition(self.root,self.launcher)
        self.assertEqual(data['EnvironmentVariables']['DEVELOPER_DIR'],'/Applications/Xcode.app/Contents/Developer')
