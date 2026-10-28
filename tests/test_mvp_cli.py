import io
import json
from contextlib import nullcontext, redirect_stdout, redirect_stderr
import unittest
from unittest.mock import patch
from sideloadlycmd.cli import main, build_parser


class MVPCLIContractTests(unittest.TestCase):
    def test_offline_prepare_does_not_require_account_argument(self):
        args=build_parser().parse_args(['prepare','app.ipa','--offline','--output','ready',
                                       '--device','phone','--team','TESTTEAM01','--bundle-id','com.example.app'])
        self.assertTrue(args.offline)

    def test_install_success_does_not_require_refresh_and_preserves_incomplete_codes(self):
        for device_state,expected,extra in [('version_and_build_verified',0,{}),('unverified',3,{}),
                                           ('unverified',31,{'exit_code':31,'state':'install_completion_unknown'})]:
            with self.subTest(device_state=device_state,expected=expected):
                result={'command':'install-prepared','state':'installed_refresh_unverified',
                        'final_bundle_id':'com.example.app','device_app':device_state,
                        'refresh':'not_registered_by_native_cli',**extra}
                output=io.StringIO()
                with patch('sideloadlycmd.xcode_backend.install_prepared',return_value=result), \
                     patch('sideloadlycmd.cli._install_mutex',return_value=nullcontext()), \
                     redirect_stdout(output),redirect_stderr(io.StringIO()):
                    code=main(['install-prepared','ready','--device','phone','--team','TESTTEAM01',
                               '--bundle-id','com.example.app','--json'])
                self.assertEqual(code,expected)
                self.assertEqual(json.loads(output.getvalue())['device_app'],device_state)

    def test_successful_registration_returns_zero_without_claiming_renewal(self):
        result={'command':'refresh-register','state':'native_refresh_registered','job_id':'fixture','final_bundle_id':'com.example.app','renewal':'not_tested'}
        with patch('sideloadlycmd.refresh.register',return_value=result),patch('sideloadlycmd.cli._install_mutex',return_value=nullcontext()),redirect_stdout(io.StringIO()) as output,redirect_stderr(io.StringIO()):
            code=main(['refresh-register','ready','--source-ipa','source.ipa','--device','phone','--account','xcode','--json'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['renewal'],'not_tested')
