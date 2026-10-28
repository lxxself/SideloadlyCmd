from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from sideloadlycmd.xcode_backend import _renew_profile_cache


class ProfileCacheRenewalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.cache=self.home/'cache'; self.cache.mkdir()
        self.target=self.cache/'target.mobileprovision'; self.target.write_bytes(b'old-profile')
        self.shared=self.cache/'shared.mobileprovision'; self.shared.write_bytes(b'shared-profile')
        self.identity=hashlib.sha1(b'certificate').hexdigest().upper()
        self.data={'ExpirationDate':datetime.now(timezone.utc)+timedelta(days=2),
                   'TeamIdentifier':['TESTTEAM01'],'ProvisionedDevices':['target-device'],
                   'DeveloperCertificates':[b'certificate'],
                   'Entitlements':{'application-identifier':'TESTTEAM01.com.example.app'}}
        self.home_patch=patch('sideloadlycmd.xcode_backend.Path.home',return_value=self.home)
        self.home_patch.start(); self.addCleanup(self.home_patch.stop)
        def decode(path,deadline):
            data=dict(self.data)
            if path==self.shared: data['Entitlements']={'application-identifier':'TESTTEAM01.*'}
            return data
        self.decode_patch=patch('sideloadlycmd.xcode_backend._decode_profile',side_effect=decode)
        self.decode_patch.start(); self.addCleanup(self.decode_patch.stop)

    def context(self):
        return _renew_profile_cache([self.cache],'TESTTEAM01','com.example.app','target-device',{self.identity},time.monotonic()+30)

    def test_only_exact_app_cache_is_removed_and_private_backup_is_retained(self):
        with self.context():
            self.assertFalse(self.target.exists())
            self.assertEqual(self.shared.read_bytes(),b'shared-profile')
        base=self.home/'Library/Application Support/SideloadlyCmd/profile-cache-backups'
        backups=list(base.glob('renew-*')); self.assertEqual(len(backups),1)
        self.assertEqual((backups[0]/'0.mobileprovision').read_bytes(),b'old-profile')
        self.assertEqual((backups[0]/'0.mobileprovision').stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads((backups[0]/'manifest.json').read_text())['state'],'cache_refresh_completed')

    def test_provisioning_failure_restores_old_cache_without_touching_shared_profile(self):
        with self.assertRaises(RuntimeError):
            with self.context():
                raise RuntimeError('simulated provisioning failure')
        self.assertEqual(self.target.read_bytes(),b'old-profile')
        self.assertEqual(self.shared.read_bytes(),b'shared-profile')

    def test_failure_never_overwrites_another_process_new_cache_entry(self):
        with self.assertRaises(RuntimeError):
            with self.context():
                self.target.write_bytes(b'newly-downloaded')
                raise RuntimeError('simulated failure')
        self.assertEqual(self.target.read_bytes(),b'newly-downloaded')
        journal=next((self.home/'Library/Application Support/SideloadlyCmd/profile-cache-backups').glob('*/manifest.json'))
        self.assertTrue(json.loads(journal.read_text())['restore_conflicts'])

    def test_no_matching_device_never_removes_a_profile(self):
        self.data['ProvisionedDevices']=['another-device']
        with self.context():
            self.assertTrue(self.target.exists())
        self.assertFalse((self.home/'Library/Application Support/SideloadlyCmd/profile-cache-backups').exists())
