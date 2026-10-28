from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch

from sideloadlycmd.errors import CliError
from sideloadlycmd.xcode_backend import _profile_usable, _rewrite_identifiers, select_account


class XcodeBindingTests(unittest.TestCase):
    def profile(self):
        return {'ExpirationDate': datetime.now(timezone.utc) + timedelta(days=1),
                'TeamIdentifier': ['TESTTEAM01'], 'ProvisionedDevices': ['target'],
                'DeveloperCertificates': [b'public-certificate'],
                'Entitlements': {'application-identifier': 'TESTTEAM01.com.example.app'}}

    def test_profile_requires_all_four_identity_bindings_and_validity(self):
        fingerprint = hashlib.sha1(b'public-certificate').hexdigest().upper()
        identities = {fingerprint}
        self.assertEqual(_profile_usable(self.profile(), 'TESTTEAM01', 'com.example.app', 'target', identities), fingerprint)
        for team, bundle, device, keys in [('OTHERTEAM1','com.example.app','target',identities),
                                          ('TESTTEAM01','com.other.app','target',identities),
                                          ('TESTTEAM01','com.example.app','other',identities),
                                          ('TESTTEAM01','com.example.app','target',set())]:
            self.assertIsNone(_profile_usable(self.profile(), team, bundle, device, keys))
        expired = self.profile(); expired['ExpirationDate'] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertIsNone(_profile_usable(expired, 'TESTTEAM01', 'com.example.app', 'target', identities))

    def test_rewrite_keeps_extension_prefix_and_unrelated_values(self):
        original = {'CFBundleIdentifier':'com.example.app.widget', 'attributes':['com.example.app','unrelated']}
        expected = {'CFBundleIdentifier':'com.example.app.TEAM.widget', 'attributes':['com.example.app.TEAM','unrelated']}
        self.assertEqual(_rewrite_identifiers(original, 'com.example.app', 'com.example.app.TEAM'), expected)
        self.assertEqual(original['CFBundleIdentifier'], 'com.example.app.widget')

    def test_shared_team_in_two_accounts_is_rejected(self):
        prefs = {'DVTDeveloperAccountManagerAppleIDLists':{'group':[{'identifier':'one'},{'identifier':'two'}]},
                 'IDEProvisioningTeamByIdentifier': {'one':[{'teamID':'TESTTEAM01'}],'two':[{'teamID':'TESTTEAM01'}]}}
        with patch.object(Path, 'read_bytes', return_value=plistlib.dumps(prefs)):
            with self.assertRaises(CliError): select_account('xcode', 'TESTTEAM01')

    def test_empty_account_list_is_not_treated_as_valid_team_cache(self):
        prefs = {'DVTDeveloperAccountManagerAppleIDLists':{'group':[]},
                 'IDEProvisioningTeamByIdentifier':{'old':[{'teamID':'TESTTEAM01'}]}}
        with patch.object(Path,'read_bytes',return_value=plistlib.dumps(prefs)):
            with self.assertRaises(CliError) as caught: select_account('xcode','TESTTEAM01')
        self.assertEqual(caught.exception.category,'xcode_account_missing')

    def test_explicit_account_mismatch_rejected_even_with_single_team_owner(self):
        prefs = {'DVTDeveloperAccountManagerAppleIDLists':{'group':[{'identifier':'one'}]},
                 'IDEProvisioningTeamByIdentifier': {'one':[{'teamID':'TESTTEAM01'}]}}
        with patch.object(Path, 'read_bytes', return_value=plistlib.dumps(prefs)):
            self.assertEqual(select_account('xcode', 'TESTTEAM01'), 'one')
            self.assertEqual(select_account('xcode:one', 'TESTTEAM01'), 'one')
            with self.assertRaises(CliError): select_account('xcode:other', 'TESTTEAM01')

    def test_offline_missing_profile_cannot_fall_back_to_xcode_or_modify_cache(self):
        import tempfile, time
        from sideloadlycmd.xcode_backend import obtain_profile
        with tempfile.TemporaryDirectory() as tmp, patch('sideloadlycmd.xcode_backend.Path.home',return_value=Path(tmp)), \
             patch('sideloadlycmd.xcode_backend._identity_hashes',return_value=set()), \
             patch('sideloadlycmd.xcode_backend._run') as native, \
             patch('sideloadlycmd.xcode_backend._renew_profile_cache') as renewal:
            with self.assertRaises(CliError) as caught:
                obtain_profile('TESTTEAM01','com.example.app','device',time.monotonic()+10,Path(tmp),offline=True)
        self.assertEqual(caught.exception.category,'cached_profile_missing')
        native.assert_not_called(); renewal.assert_not_called()

    def test_offline_prepare_skips_account_selection(self):
        import tempfile
        from sideloadlycmd.xcode_backend import prepare
        with tempfile.TemporaryDirectory() as tmp, patch('sideloadlycmd.xcode_backend.select_account') as account, \
             patch('sideloadlycmd.xcode_backend.resolve_device',side_effect=CliError('not connected',10,'device_unavailable')):
            with self.assertRaises(CliError) as caught:
                prepare(Path(tmp)/'input.ipa',Path(tmp)/'output','xcode','TESTTEAM01','com.example.app','exact','phone',False,10,offline=True)
        account.assert_not_called()
        self.assertEqual(caught.exception.category,'device_unavailable')

    def test_online_provisioning_registers_exact_destination_and_redacts_failure(self):
        import tempfile,time,subprocess
        from sideloadlycmd.xcode_backend import obtain_profile
        with tempfile.TemporaryDirectory() as tmp, patch('sideloadlycmd.xcode_backend.Path.home',return_value=Path(tmp)), \
             patch('sideloadlycmd.xcode_backend._identity_hashes',return_value=set()), \
             patch('sideloadlycmd.xcode_backend._provisioning_build',return_value=subprocess.CompletedProcess([],65,b'',b'errSecInternalComponent secret-fixture owner@example.invalid')) as build:
            with self.assertRaises(CliError) as caught:
                obtain_profile('TESTTEAM01','com.example.app','selected-device',time.monotonic()+10,Path(tmp))
        command=build.call_args.args[0]
        self.assertIn('-allowProvisioningDeviceRegistration',command)
        self.assertIn('-allowProvisioningUpdates',command)
        self.assertEqual(command[command.index('-destination')+1],'id=selected-device')
        self.assertIn('keychain_signing_failed',caught.exception.message)
        self.assertNotIn('secret-fixture',caught.exception.message)
        self.assertNotIn('owner@example.invalid',caught.exception.message)

    def test_online_global_clt_fails_before_account_source_or_device_actions(self):
        import subprocess
        from sideloadlycmd.xcode_backend import prepare
        with patch('sideloadlycmd.xcode_backend._run',return_value=subprocess.CompletedProcess([],0,b'/Library/Developer/CommandLineTools\n',b'')),patch('sideloadlycmd.xcode_backend.select_account') as account,patch('sideloadlycmd.xcode_backend.resolve_device') as device:
            with self.assertRaises(CliError) as caught:
                prepare(Path('/missing.ipa'),Path('/missing-output'),'xcode','TESTTEAM01','com.example.app','exact','phone',False,30)
        self.assertEqual(caught.exception.category,'global_xcode_not_selected')
        account.assert_not_called();device.assert_not_called()
