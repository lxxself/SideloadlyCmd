import io
import json
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from sideloadlycmd.cli import main
from sideloadlycmd.progress import Progress, stage


class ProgressTests(unittest.TestCase):
    def test_wait_reports_current_stage_and_stops_before_close(self):
        rows = []
        waiting = threading.Event()
        def emit(event, **data):
            rows.append((event, data))
            if event == 'command_waiting':
                waiting.set()
        progress = Progress(emit, 'prepare', interval=0.01)
        progress.start()
        try:
            stage('provisioning', 'Obtaining a profile', current=1, total=2)
            self.assertTrue(waiting.wait(2))
        finally:
            progress.close()
        self.assertFalse(progress.thread.is_alive())
        self.assertEqual(rows[1][1]['stage'], 'provisioning')
        self.assertEqual(rows[1][1]['total'], 2)
        self.assertEqual(rows[-1][0], 'command_waiting')
        stage('outside', 'No active progress scope')
        self.assertEqual(rows[-1][0], 'command_waiting')

    def test_cli_keeps_result_json_separate_from_progress(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('sideloadlycmd.teams.list_teams', return_value=[]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(['teams', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())['state'], 'saved_teams_read')
        events = [json.loads(line) for line in stderr.getvalue().splitlines()]
        self.assertEqual(events[0]['event'], 'command_progress')
        self.assertEqual(events[0]['command'], 'teams')
        self.assertNotIn('command_waiting', [row['event'] for row in events])
