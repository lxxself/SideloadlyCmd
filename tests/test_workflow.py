from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sideloadlycmd import workflow
from sideloadlycmd.errors import CliError,ExitCode

class WorkflowTests(unittest.TestCase):
    def test_dry_run_does_not_prepare_install_or_create_output(self):
        with patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','unique_personal')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'plan_components',return_value={'removed_components':[],'warnings':[]}),patch.object(workflow,'prepare') as prepare,patch.object(workflow,'install_prepared') as install,patch.object(workflow.tempfile,'mkdtemp') as directory:
            result,code=workflow.sideload(Path('app.ipa'),'phone',dry_run=True)
        self.assertEqual(result['final_bundle_id'],'com.example.app.TEAM000001')
        self.assertEqual(code,0)
        prepare.assert_not_called();install.assert_not_called();directory.assert_not_called()
    def test_install_uses_prepared_final_id_and_keeps_partial_completion(self):
        with patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','explicit')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'prepare',return_value={'final_bundle_id':'com.example.app.TEAM000001'}) as prepare,patch.object(workflow,'install_prepared',return_value={'state':'installed_device_unverified','device_app':'unverified','install':'completed_this_run'}) as install:
            result,code=workflow.sideload(Path('app.ipa'),'phone',output=Path('ready'),strip_watch=True)
        self.assertEqual(install.call_args.args[3],'com.example.app.TEAM000001')
        self.assertEqual(prepare.call_args.args[5],'automatic')
        self.assertEqual(code,3)
        self.assertEqual(result['prepared'],'ready')
    def test_preparation_failure_never_installs(self):
        with patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','explicit')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'prepare',side_effect=CliError('failure',ExitCode.NEEDS_USER,'fixture')),patch.object(workflow,'install_prepared') as install:
            with self.assertRaises(CliError): workflow.sideload(Path('app.ipa'),'phone',output=Path('ready'))
        install.assert_not_called()
    def test_install_timeout_stays_unknown_and_reports_prepared_path(self):
        with patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','explicit')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'prepare',return_value={'final_bundle_id':'com.example.app.TEAM000001'}),patch.object(workflow,'install_prepared',return_value={'state':'install_completion_unknown','device_app':'unverified','install':'completion_unknown','exit_code':31}):
            result,code=workflow.sideload(Path('app.ipa'),'phone',output=Path('ready'))
        self.assertEqual(code,31)
        self.assertEqual(result['state'],'install_completion_unknown')
        self.assertEqual(result['prepared'],'ready')

    def test_device_loss_after_preparation_keeps_recovery_path(self):
        with patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','explicit')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'prepare',return_value={'final_bundle_id':'com.example.app.TEAM000001'}),patch.object(workflow,'install_prepared',side_effect=CliError('device disappeared',ExitCode.PREFLIGHT,'device_not_unique')):
            result,code=workflow.sideload(Path('app.ipa'),'phone',output=Path('ready'))
        self.assertEqual(code,10)
        self.assertEqual(result['prepared'],'ready')
        self.assertEqual(result['state'],'prepared_install_unverified')
    def test_automatic_device_is_pinned_for_prepare_and_install(self):
        with patch.object(workflow,'resolve_device',return_value='pinned-usb-id') as resolve,patch.object(workflow,'require_global_xcode'),patch.object(workflow,'select_team',return_value=('TEAM000001','unique_personal')),patch.object(workflow,'inspect_ipa',return_value=SimpleNamespace(bundle_id='com.example.app',app_directory='Payload/Example.app')),patch.object(workflow,'prepare',return_value={'final_bundle_id':'com.example.app.TEAM000001'}) as prepare,patch.object(workflow,'install_prepared',return_value={'device_app':'version_and_build_verified','state':'installed_refresh_unverified'}) as install:
            result,code=workflow.sideload(Path('app.ipa'),output=Path('ready'))
        self.assertEqual(resolve.call_count,1)
        self.assertEqual(prepare.call_args.args[6],'pinned-usb-id')
        self.assertEqual(install.call_args.args[1],'pinned-usb-id')
        self.assertEqual(result['device_selection'],'unique_usb')
        self.assertEqual(code,0)
