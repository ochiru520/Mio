"""Bridge contracts exercised with synthetic windows, never a desktop launch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desktop import launcher


class WindowBridgeBoundaryTests(unittest.TestCase):
    def bridge(self):
        bridge = launcher.DesktopBridge('agent')
        native = Mock()
        native.Handle.ToInt64.return_value = 123
        bridge.attach_window(Mock(native=native), None)
        return bridge

    def test_native_drag_install_failure_never_posts_a_move(self):
        bridge = self.bridge()
        with (
            patch.object(launcher.ctypes.windll.user32, 'IsWindow', return_value=1),
            patch.object(launcher, '_install_native_hit_test', return_value=False) as install,
            patch.object(launcher.ctypes.windll.user32, 'PostMessageW') as post,
        ):
            self.assertFalse(bridge.window_drag()['ok'])
        install.assert_called_once_with(123)
        post.assert_not_called()

    def test_resize_direction_cannot_change_during_a_gesture(self):
        bridge = self.bridge()
        bridge._resize_gesture = ('gesture', 'left', 10, 20, 800, 700, 1)
        with (
            patch.object(launcher.ctypes.windll.user32, 'IsZoomed', return_value=0),
            patch.object(launcher.ctypes.windll.user32, 'SetWindowPos') as move,
        ):
            self.assertFalse(bridge.window_resize_by('right', 5, 0, 'gesture')['ok'])
        move.assert_not_called()

    def test_failed_move_ends_a_finished_gesture(self):
        bridge = self.bridge()
        bridge._move_gesture = ('gesture', 10, 20, 1)
        with (
            patch.object(launcher.ctypes.windll.user32, 'IsZoomed', return_value=0),
            patch.object(launcher.ctypes.windll.user32, 'SetWindowPos', return_value=0),
        ):
            self.assertFalse(bridge.window_move_by(5, 6, 'gesture', True)['ok'])
        self.assertIsNone(bridge._move_gesture)

    def test_close_policy_is_evaluated_before_window_destruction(self):
        bridge = self.bridge()
        window = bridge._window
        bridge._hide_window = Mock(return_value=False)
        bridge.window_control('close')
        window.destroy.assert_not_called()
        bridge._hide_window.return_value = True
        bridge.window_control('close')
        window.destroy.assert_called_once_with()

    def test_workspace_switch_restores_target_before_hiding_source(self):
        events = []
        main_window = Mock(hide=lambda: events.append('hide-main'))
        agent_window = Mock(show=lambda: events.append('show-agent'), restore=lambda: events.append('restore-agent'))
        controller = launcher.WorkspaceWindowController()
        controller.attach('main', main_window)
        controller.attach('agent', agent_window)
        self.assertTrue(controller.switch('agent')['ok'])
        self.assertEqual(events, ['show-agent', 'restore-agent', 'hide-main'])

    def test_workspace_destroy_continues_after_one_window_fails(self):
        controller = launcher.WorkspaceWindowController()
        main_window = Mock()
        main_window.destroy.side_effect = OSError('already closed')
        agent_window = Mock()
        controller.attach('main', main_window)
        controller.attach('agent', agent_window)
        with self.assertLogs(level='ERROR'):
            controller.destroy_all()
        controller.destroy_all()
        agent_window.destroy.assert_called_once_with()
        self.assertFalse(controller.switch('agent')['ok'])

    def test_pywebview_exports_inherited_methods_with_unchanged_parameters(self):
        from webview import util

        bridge = launcher.DesktopBridge()
        window = types.SimpleNamespace(
            _js_api=bridge, _functions={}, _expose_lock=threading.Lock(),
            run_js=Mock(), events=types.SimpleNamespace(
                before_load=Mock(), _pywebviewready=Mock(), loaded=Mock(),
            ),
        )
        with (
            patch.object(util, 'load_js_files', return_value=('bootstrap', '%(functions)s')),
            patch.object(util, 'Thread', side_effect=lambda target: types.SimpleNamespace(start=target)),
        ):
            util.inject_pywebview('edgechromium', window)
        exported = {item['func']: item['params'] for item in json.loads(window.run_js.call_args.args[0])}
        expected = {
            'attach_window': ['window', 'hide_window'],
            'window_control': ['action'],
            'window_drag': [],
            'window_resize': ['direction'],
            'window_move_by': ['offset_x', 'offset_y', 'gesture_id', 'finished'],
            'window_resize_by': ['direction', 'offset_x', 'offset_y', 'gesture_id', 'finished'],
            'select_agent_path': ['kind'],
        }
        for name, parameters in expected.items():
            self.assertEqual(exported[name], parameters)
        self.assertFalse(any(name.startswith('_') for name in exported))
        window.events.loaded.set.assert_called_once_with()

    def test_direct_launcher_import_uses_the_bundled_module_layout(self):
        desktop = Path(__file__).resolve().parent
        code = (
            'import sys; sys.path.insert(0, sys.argv[1]); import launcher; '
            'assert launcher.DesktopBridge().select_agent_path()["ok"] is False; '
            'assert launcher.WindowBridgeMixin.__module__ == "window_bridge"; '
            'assert launcher.select_agent_path.__module__ == "local_path_picker"'
        )
        with tempfile.TemporaryDirectory(prefix='mio-import-fixture-') as directory:
            result = subprocess.run(
                [sys.executable, '-I', '-c', code, str(desktop)],
                cwd=directory, env={**os.environ, 'MIO_DESKTOP_STATE_DIR': directory},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class WindowBridgePackagingTests(unittest.TestCase):
    def test_packaging_analysis_includes_the_extracted_modules(self):
        desktop = Path(__file__).resolve().parent
        analysis = Mock(return_value=types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
        hooks = types.ModuleType('PyInstaller.utils.hooks')
        hooks.collect_data_files = Mock(return_value=[])
        hooks.collect_submodules = Mock(return_value=[])
        namespace = {
            'SPECPATH': str(desktop), 'Analysis': analysis,
            'PYZ': Mock(), 'EXE': Mock(), 'COLLECT': Mock(),
        }
        source = (desktop / 'mio-agent.spec').read_text(encoding='utf-8')
        with patch.dict(sys.modules, {'PyInstaller.utils.hooks': hooks}), patch.object(Path, 'is_file', return_value=True):
            exec(compile(source, str(desktop / 'mio-agent.spec'), 'exec'), namespace)
        options = analysis.call_args.kwargs
        self.assertIn(str(desktop), options['pathex'])
        for name in ('window_bridge', 'local_path_picker'):
            self.assertIn(name, options['hiddenimports'])
            self.assertTrue((desktop / (name + '.py')).is_file())
