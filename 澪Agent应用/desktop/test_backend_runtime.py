from __future__ import annotations

import ast
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from desktop import backend_runtime, launcher


class BackendLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('isolated-runtime').resolve()
        self.listener = Mock()
        self.server = Mock()
        self.thread = Mock(ident=123)
        self.thread.is_alive.return_value = True
        self.uvicorn = types.ModuleType('uvicorn')
        self.uvicorn.Config = Mock()
        self.uvicorn.Server = Mock(return_value=self.server)
        app = types.ModuleType('app')
        app.__path__ = []
        main = types.ModuleType('app.main')
        main.app = object()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {
            'uvicorn': self.uvicorn, 'app': app, 'app.main': main,
        }))
        self.stack.enter_context(patch.object(launcher, '_BACKEND_SOCKET', self.listener))
        self.stack.enter_context(patch.object(launcher, 'PORT', 8000))
        self.stack.enter_context(patch.object(launcher, '_port_is_open', return_value=False))
        self.stack.enter_context(patch.object(backend_runtime.threading, 'Thread', return_value=self.thread))
        self.runtime = launcher.BackendRuntime(self.root)

    def test_start_reuses_reserved_socket_and_exposes_thread_only_after_health(self):
        with patch.object(launcher, '_health', side_effect=[None, {'project_root': str(self.root)}]):
            self.runtime.start()
        self.assertIs(self.runtime.current_thread(), self.thread)
        self.assertIs(self.runtime.server, self.server)
        backend_runtime.threading.Thread.assert_called_once_with(
            target=self.server.run, kwargs={'sockets': [self.listener]},
            name='mio-backend', daemon=True,
        )
        self.thread.start.assert_called_once_with()
        self.listener.close.assert_not_called()

    def test_external_backend_is_not_stopped_or_claimed(self):
        with patch.object(launcher, '_health', return_value={'project_root': str(self.root)}):
            self.runtime.start()
        self.assertIsNone(self.runtime.current_thread())
        self.assertTrue(self.runtime.stop())
        self.uvicorn.Server.assert_not_called()
        self.listener.close.assert_called_once_with()

    def test_other_runtime_and_unhealthy_occupied_port_are_rejected(self):
        with patch.object(launcher, '_health', return_value={'project_root': str(self.root / 'other')}):
            with self.assertRaisesRegex(RuntimeError, '另一份 Mio'):
                self.runtime.start()
        with patch.object(launcher, '_health', return_value=None), \
                patch.object(launcher, '_port_is_open', return_value=True):
            with self.assertRaisesRegex(RuntimeError, '其他程序占用'):
                self.runtime.start()
        self.uvicorn.Server.assert_not_called()

    def test_missing_listener_is_reserved_without_silent_port_fallback(self):
        with patch.object(launcher, '_BACKEND_SOCKET', None), \
                patch.object(launcher, '_bind_backend_listener', return_value=self.listener) as bind, \
                patch.object(launcher, '_health', side_effect=[None, {'project_root': str(self.root)}]):
            self.runtime.start()
            self.assertIs(launcher._BACKEND_SOCKET, self.listener)
        bind.assert_called_once_with(8000, allow_fallback=False)

    def test_startup_timeout_keeps_resources_for_bounded_shutdown(self):
        with patch.object(launcher, '_health', return_value=None), \
                patch.object(backend_runtime.time, 'monotonic', side_effect=[0, 26]):
            with self.assertRaisesRegex(RuntimeError, '后端没有正常启动'):
                self.runtime.start()
        self.assertTrue(self.server.should_exit)
        self.assertIs(self.runtime.current_thread(), self.thread)
        self.assertFalse(self.runtime.stop(timeout=0.1))
        self.thread.join.assert_called_once_with(timeout=0.1)
        self.listener.close.assert_not_called()
        self.thread.is_alive.return_value = False
        self.assertTrue(self.runtime.stop(timeout=0))
        self.listener.close.assert_called_once_with()
        self.assertIsNone(launcher._BACKEND_SOCKET)

    def test_probe_exception_keeps_thread_for_shutdown_and_preserves_cause(self):
        error = OSError('health transport failed')
        with patch.object(launcher, '_health', side_effect=[None, error]):
            with self.assertRaises(backend_runtime.BackendStartupError) as failure:
                self.runtime.start()
        self.assertIs(failure.exception.__context__, error)
        self.assertIs(self.runtime.current_thread(), self.thread)
        self.assertTrue(self.server.should_exit)

    def test_thread_start_failure_releases_socket_without_joining_unstarted_thread(self):
        self.thread.start.side_effect = RuntimeError('thread could not start')
        self.thread.ident = None
        self.thread.is_alive.return_value = False
        with patch.object(launcher, '_health', return_value=None):
            with self.assertRaises(RuntimeError):
                self.runtime.start()
        self.assertTrue(self.runtime.stop())
        self.thread.join.assert_not_called()
        self.listener.close.assert_called_once_with()

    def test_config_failure_still_releases_reserved_socket(self):
        self.uvicorn.Config.side_effect = ValueError('invalid config')
        with patch.object(launcher, '_health', return_value=None):
            with self.assertRaises(ValueError):
                self.runtime.start()
        self.assertTrue(self.runtime.stop())
        self.listener.close.assert_called_once_with()

    def test_shutdown_timeout_prevents_second_server_and_preserves_listener(self):
        self.runtime.server, self.runtime.thread = self.server, self.thread
        with patch.object(launcher, '_start_backend') as start:
            self.assertFalse(self.runtime.recover('live thread'))
        start.assert_not_called()
        self.listener.close.assert_not_called()
        self.assertIs(self.runtime.current_thread(), self.thread)

    def test_stop_is_idempotent_after_thread_exits(self):
        self.runtime.server, self.runtime.thread = self.server, self.thread
        self.thread.is_alive.return_value = False
        self.assertTrue(self.runtime.stop(timeout=-1))
        self.assertTrue(self.runtime.stop())
        self.thread.join.assert_called_once_with(timeout=0.0)
        self.listener.close.assert_called_once_with()


class BackendWatchdogBoundaryTests(unittest.TestCase):
    def watch(self, *, health, recover=True, attempts=2, times=None):
        stop = Mock()
        stop.wait.side_effect = [False] * len(health) + [True]
        runtime = Mock()
        runtime.current_thread.return_value.is_alive.return_value = False
        runtime.recover.return_value = recover
        record = Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(launcher, '_backend_health_matches_runtime', side_effect=health))
            stack.enter_context(patch.object(launcher, '_port_is_open', return_value=False))
            stack.enter_context(patch.object(launcher, '_record_backend_recovery_state', record))
            if times is not None:
                stack.enter_context(patch.object(backend_runtime.time, 'monotonic', side_effect=times))
            launcher._watch_backend_health(
                stop, Path('runtime'), failure_threshold=1, backend_runtime=runtime,
                diagnostics_provider=lambda: {}, recovery_max_attempts=attempts,
                recovery_window_seconds=10,
            )
        return runtime, record

    def test_repeated_outage_opens_circuit_without_third_restart(self):
        runtime, record = self.watch(health=[False, False, False], times=[0, 1, 2])
        self.assertEqual(runtime.recover.call_count, 2)
        self.assertEqual([call.args[0] for call in record.call_args_list], ['recovered', 'recovered', 'fused'])
        self.assertEqual(record.call_args.kwargs['attempts_in_window'], 2)

    def test_old_recovery_attempts_expire(self):
        runtime, record = self.watch(health=[False, False], attempts=1, times=[0, 11])
        self.assertEqual(runtime.recover.call_count, 2)
        self.assertEqual(record.call_args.args[0], 'recovered')
        self.assertEqual(record.call_args.kwargs['attempts_in_window'], 1)

    def test_failed_restart_stops_watchdog_and_records_failure(self):
        runtime, record = self.watch(health=[False], recover=False)
        runtime.recover.assert_called_once()
        self.assertEqual(record.call_args.args[0], 'recovery_failed')

    def test_unmanaged_outage_records_diagnostic_without_starting_backend(self):
        stop, record = Mock(), Mock()
        stop.wait.side_effect = [False, True]
        with patch.object(launcher, '_backend_health_matches_runtime', return_value=False), \
                patch.object(launcher, '_port_is_open', return_value=False), \
                patch.object(launcher, '_record_backend_recovery_state', record), \
                patch.object(launcher, '_start_backend') as start:
            launcher._watch_backend_health(stop, Path('runtime'), failure_threshold=1,
                                           diagnostics_provider=lambda: {})
        start.assert_not_called()
        self.assertEqual(record.call_args.args[0], 'unmanaged')


class BackendPackagingTests(unittest.TestCase):
    def test_standalone_launcher_import_uses_desktop_module_search_path(self):
        desktop = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='mio-desktop-import-') as state:
            environment = dict(os.environ, MIO_DESKTOP_STATE_DIR=state, PYTHONPATH=str(desktop))
            result = subprocess.run([
                sys.executable, '-c',
                "import launcher; assert launcher.EmbeddedBackendRuntime.__module__ == 'backend_runtime'",
            ], cwd=state, env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frozen_analysis_includes_module_and_desktop_search_path(self):
        desktop = Path(__file__).resolve().parent
        spec = ast.parse((desktop / 'mio-agent.spec').read_text(encoding='utf-8'))
        hidden = next(node.value for node in ast.walk(spec) if isinstance(node, ast.AugAssign)
                      and isinstance(node.target, ast.Name) and node.target.id == 'hiddenimports'
                      and isinstance(node.value, ast.List))
        self.assertIn('backend_runtime', ast.literal_eval(hidden))
        analysis = next(node for node in ast.walk(spec) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'Analysis')
        pathex = next(keyword.value for keyword in analysis.keywords if keyword.arg == 'pathex')
        self.assertIn('str(desktop)', ast.unparse(pathex))


if __name__ == '__main__':
    unittest.main()
