import errno
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desktop import launcher


class BackendPortTests(unittest.TestCase):
    def test_reserved_windows_port_uses_kernel_selected_port_and_keeps_socket(self):
        listener = Mock()
        listener.bind.side_effect = [PermissionError(errno.EACCES, 'excluded port'), None]
        listener.getsockname.return_value = ('127.0.0.1', 51234)
        with patch.object(launcher.socket, 'socket', return_value=listener):
            result = launcher._bind_backend_listener(8000, allow_fallback=True)
        self.assertIs(result, listener)
        self.assertEqual(listener.bind.call_args_list[0].args[0], ('127.0.0.1', 8000))
        self.assertEqual(listener.bind.call_args_list[1].args[0], ('127.0.0.1', 0))
        listener.close.assert_not_called()

    def test_explicit_port_does_not_silently_change(self):
        listener = Mock()
        listener.bind.side_effect = PermissionError(errno.EACCES, 'excluded port')
        with patch.object(launcher.socket, 'socket', return_value=listener):
            with self.assertRaises(PermissionError):
                launcher._bind_backend_listener(8000, allow_fallback=False)
        self.assertEqual(listener.bind.call_count, 1)
        listener.close.assert_called_once()

    def test_unexpected_bind_error_is_not_hidden(self):
        listener = Mock()
        listener.bind.side_effect = OSError(errno.ENETDOWN, 'network down')
        with patch.object(launcher.socket, 'socket', return_value=listener):
            with self.assertRaises(OSError):
                launcher._bind_backend_listener(8000, allow_fallback=True)
        self.assertEqual(listener.bind.call_count, 1)

    def test_reserved_listener_cannot_be_stolen_before_server_starts(self):
        listener = launcher._bind_backend_listener(0, allow_fallback=False)
        try:
            with socket.socket() as contender:
                with self.assertRaises(OSError):
                    contender.bind(listener.getsockname())
        finally:
            listener.close()

    def test_endpoint_updates_children_and_preserves_instance_channel(self):
        listener = Mock()
        listener.getsockname.return_value = ('127.0.0.1', 51234)
        channels = (launcher.MUTEX_NAME, launcher.SHOW_EVENT_NAME, launcher.PET_CHAT_EVENT_NAME)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'MIO_PET_API_BASE': 'http://127.0.0.1:8000'}), \
                patch.object(launcher, 'STATE_DIR', Path(tmp)), patch.object(launcher, 'PORT', 8000), \
                patch.object(launcher, '_BACKEND_SOCKET', None), patch.object(launcher, '_PORT_EXPLICIT', False), \
                patch.object(launcher, '_bind_backend_listener', return_value=listener):
            launcher._prepare_backend_endpoint()
            self.assertEqual(launcher.PORT, 51234)
            self.assertEqual(os.environ['MIO_PORT'], '51234')
            self.assertEqual(os.environ['MIO_PET_API_BASE'], 'http://127.0.0.1:51234')
            self.assertEqual(os.environ['MIO_INSTANCE_PORT'], str(launcher._INSTANCE_PORT))
            self.assertTrue((Path(tmp) / '后端地址.json').is_file())
            self.assertEqual(channels, (launcher.MUTEX_NAME, launcher.SHOW_EVENT_NAME, launcher.PET_CHAT_EVENT_NAME))

    def test_explicit_pet_endpoint_is_preserved(self):
        listener = Mock()
        listener.getsockname.return_value = ('127.0.0.1', 51234)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'MIO_PET_API_BASE': 'http://127.0.0.1:9001'}), \
                patch.object(launcher, 'STATE_DIR', Path(tmp)), patch.object(launcher, 'PORT', 8000), \
                patch.object(launcher, '_BACKEND_SOCKET', None), \
                patch.object(launcher, '_bind_backend_listener', return_value=listener):
            launcher._prepare_backend_endpoint()
            self.assertEqual(os.environ['MIO_PET_API_BASE'], 'http://127.0.0.1:9001')
