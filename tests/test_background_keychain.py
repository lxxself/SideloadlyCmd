import os
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

from sideloadlycmd import background_keychain as keychain
from sideloadlycmd.errors import CliError, ExitCode


class BackgroundKeychainTests(unittest.TestCase):
    def test_public_security_flags_are_visible_but_credentials_are_redacted(self):
        from sideloadlycmd.cli import _redact
        row = _redact({'password_persisted':False,'token_available':True,'password':'never-print'})
        self.assertIs(row['password_persisted'],False)
        self.assertIs(row['token_available'],True)
        self.assertEqual(row['password'],'<redacted>')

    def test_unlock_without_terminal_does_not_launch_or_read_password(self):
        with patch.object(keychain.sys.stdin,'isatty',return_value=False), \
             patch('sideloadlycmd.xcode_backend._run') as run, patch.object(keychain.getpass,'getpass') as prompt:
            with self.assertRaises(CliError) as caught: keychain.manage('unlock',30)
        self.assertEqual(caught.exception.code,ExitCode.NEEDS_USER)
        run.assert_not_called(); prompt.assert_not_called()

    def test_peer_identity_is_checked_against_kernel(self):
        first, second = socket.socketpair()
        try:
            self.assertEqual(keychain._peer_uid(first),os.getuid())
            self.assertEqual(keychain._peer_uid(second),os.getuid())
        finally:
            first.close(); second.close()

    def test_worker_never_sends_password_back_and_erases_mutable_buffer(self):
        secret = bytearray(b'private-fixture')
        lib = MagicMock()
        def unlock(reference,length,buffer,provided):
            self.assertEqual(bytes(buffer),b'private-fixture')
            self.assertTrue(provided)
            return 0
        lib.SecKeychainUnlock.side_effect = unlock
        connection = MagicMock()
        with patch.object(keychain,'_security',return_value=lib), \
             patch.object(keychain.socket,'socket') as factory, \
             patch.object(keychain,'_peer_uid',return_value=os.getuid()), \
             patch.object(keychain,'_receive',side_effect=[struct.pack('!I',len(secret)),secret]), \
             patch.object(keychain,'_status',side_effect=[{'unlocked':False},{'unlocked':True}]), \
             patch.object(keychain,'_send_json') as reply:
            factory.return_value.__enter__.return_value = connection
            keychain.worker('/private/local-test',30)
        self.assertEqual(secret,bytearray(len(secret)))
        self.assertNotIn('private-fixture',repr(reply.call_args_list))
        self.assertEqual(reply.call_args_list[-1].args[1]['unlock_status_code'],0)

    def test_worker_rejects_foreign_peer_before_reading_credentials(self):
        with patch.object(keychain,'_security'),patch.object(keychain.socket,'socket'), \
             patch.object(keychain,'_peer_uid',return_value=os.getuid()+1), \
             patch.object(keychain,'_receive') as receive:
            with self.assertRaises(OSError): keychain.worker('/private/local-test',30)
        receive.assert_not_called()

    def test_cleanup_timeout_does_not_replace_successful_status(self):
        import subprocess
        server=MagicMock()
        connection=MagicMock()
        server.accept.return_value=(connection,None)
        connection.__enter__.return_value=connection
        with patch.object(keychain.socket,'socket') as factory, \
             patch.object(keychain.os,'chmod'), \
             patch('sideloadlycmd.xcode_backend._run',return_value=subprocess.CompletedProcess([],0,b'',b'')), \
             patch.object(keychain,'_peer_uid',return_value=os.getuid()), \
             patch.object(keychain,'_read_json',side_effect=[{'unlocked':True},{'unlocked':True}]), \
             patch.object(keychain.subprocess,'run',side_effect=subprocess.TimeoutExpired('launchctl',1)):
            factory.return_value.__enter__.return_value=server
            result=keychain.manage('status',30)
        self.assertEqual(result['state'],'background_keychain_checked')
        self.assertLessEqual(connection.settimeout.call_args.args[0],30)
