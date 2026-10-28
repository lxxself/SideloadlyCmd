import subprocess
import time
import unittest
from unittest.mock import patch
from sideloadlycmd.native_doctor import destination_status

class DestinationTests(unittest.TestCase):
    def inspect(self,output,code=0):
        with patch('sideloadlycmd.native_doctor._run',return_value=subprocess.CompletedProcess([],code,output.encode(),b'')) as run:
            result=destination_status('chosen-device','TESTTEAM01',time.monotonic()+30)
        command=run.call_args.args[0]
        self.assertNotIn('-allowProvisioningUpdates',command)
        self.assertNotIn('-allowProvisioningDeviceRegistration',command)
        self.assertNotIn('chosen-device',str(result))
        return result
    def test_missing_platform_on_exact_device_is_reported(self):
        result=self.inspect('{ platform:iOS, id:chosen-device, error:iOS 26.2 is not installed. }')
        self.assertEqual(result['category'],'xcode_platform_unavailable')
    def test_other_device_cannot_satisfy_destination_check(self):
        self.assertEqual(self.inspect('{ platform:iOS, id:chosen-device-extra }')['category'],'xcode_device_unavailable')
    def test_available_selected_device(self):
        self.assertEqual(self.inspect('{ platform:iOS, id:chosen-device, name:fixture }')['state'],'available')
    def test_command_failure_never_becomes_available(self):
        self.assertEqual(self.inspect('{ id:chosen-device }',70)['state'],'unavailable')
