from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class NativeTerminationTests(unittest.TestCase):
    def test_sigterm_cancels_cli_and_terminates_backend_descendant(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root/'child-pid'
            backend = root/'backend'
            backend.write_text('#!'+sys.executable+'\nimport subprocess,time\nfrom pathlib import Path\n'
                               'child=subprocess.Popen(["/bin/sleep","30"])\n'
                               'Path('+repr(str(pid_file))+').write_text(str(child.pid))\ntime.sleep(30)\n')
            backend.chmod(0o700)
            process = subprocess.Popen([sys.executable,str(project/'scripts/sideloadlycmd'),'headless-doctor',
                                        '--backend',str(backend),'--timeout','30','--json'],
                                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline = time.monotonic()+10
                while not pid_file.exists() and time.monotonic()<deadline and process.poll() is None:
                    time.sleep(.05)
                self.assertTrue(pid_file.exists(),'backend did not start')
                process.send_signal(signal.SIGTERM)
                output, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode,32)
                self.assertEqual(json.loads(output)['state'],'cancelled')
                pid = int(pid_file.read_text())
                deadline = time.monotonic()+3
                while time.monotonic()<deadline:
                    try:
                        os.kill(pid,0)
                    except ProcessLookupError:
                        break
                    time.sleep(.05)
                else:
                    self.fail('backend descendant remained alive after SIGTERM')
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()
                if pid_file.exists():
                    try: os.kill(int(pid_file.read_text()),signal.SIGKILL)
                    except ProcessLookupError: pass
