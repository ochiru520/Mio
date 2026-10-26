import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from desktop import launcher


class AgentPathPickerTests(unittest.TestCase):
    def test_agent_conversation_window_validates_id_and_uses_native_close(self):
        bridge=launcher.DesktopBridge('agent')
        window=MagicMock(); webview=Mock(); webview.create_window.return_value=window
        service = Mock(require=Mock())
        require = service.require
        with patch.dict(sys.modules, {'app.conversation_state_service': service, 'webview': webview}):
            self.assertEqual(bridge.open_agent_conversation('desktop_agent_example'),{'ok':True})
        require.assert_called_once_with('desktop_agent_example')
        self.assertIn('conversation_id=desktop_agent_example',webview.create_window.call_args.args[1])
        child=webview.create_window.call_args.kwargs['js_api']
        self.assertIs(child._window,window)
        self.assertEqual(child._hide_window,window.destroy)

    def bridge(self, selected):
        bridge=launcher.DesktopBridge(); window=Mock()
        window.create_file_dialog.return_value=selected
        bridge.attach_window(window,Mock())
        return bridge,window

    def test_cancel_does_not_change_any_configuration(self):
        bridge,window=self.bridge(None)
        with patch.dict(sys.modules,{'webview':Mock(FOLDER_DIALOG='folder')}):
            self.assertEqual(bridge.select_agent_path(),{'ok':False,'canceled':True})

    def test_folder_selection_returns_selected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge,window=self.bridge([directory])
            with patch.dict(sys.modules,{'webview':Mock(FOLDER_DIALOG='folder')}):
                self.assertEqual(bridge.select_agent_path()['path'],str(Path(directory).resolve()))

    def test_workflow_selection_reads_only_chosen_json_and_bounds_size(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'workflow.json'; target.write_text('{"nodes":[]}')
            bridge,window=self.bridge([str(target)])
            with patch.dict(sys.modules,{'webview':Mock(OPEN_DIALOG='open')}):
                self.assertEqual(bridge.select_agent_path('workflow')['prompt'],{'nodes':[]})
                target.write_bytes(b' '*2_000_001)
                self.assertFalse(bridge.select_agent_path('workflow')['ok'])
                self.assertFalse(bridge.select_agent_path('arbitrary')['ok'])

    def test_unready_window_and_invalid_kind_never_show_dialog(self):
        self.assertFalse(launcher.DesktopBridge().select_agent_path()['ok'])
        bridge, window = self.bridge(None)
        self.assertFalse(bridge.select_agent_path('arbitrary')['ok'])
        window.create_file_dialog.assert_not_called()

    def test_folder_selection_accepts_single_string_and_rejects_file_or_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge, window = self.bridge(directory)
            with patch.dict(sys.modules, {'webview': Mock(FOLDER_DIALOG='folder')}):
                self.assertEqual(bridge.select_agent_path()['path'], str(Path(directory).resolve()))
                target = Path(directory) / 'file.json'
                target.write_text('{}', encoding='utf-8')
                window.create_file_dialog.return_value = (str(target),)
                self.assertFalse(bridge.select_agent_path()['ok'])
                window.create_file_dialog.return_value = [str(target.with_name('missing'))]
                self.assertFalse(bridge.select_agent_path()['ok'])

    def test_workflow_selection_accepts_bom_and_exact_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'workflow.JSON'
            content = b'\xef\xbb\xbf{"nodes":[]} '
            target.write_bytes(content + b' ' * (2_000_000 - len(content)))
            bridge, window = self.bridge([str(target)])
            with patch.dict(sys.modules, {'webview': Mock(OPEN_DIALOG='open')}):
                self.assertEqual(bridge.select_agent_path('workflow')['prompt'], {'nodes': []})
            window.create_file_dialog.assert_called_once_with(
                'open', directory=str(Path.home()), allow_multiple=False,
                file_types=('ComfyUI JSON (*.json)',),
            )

    def test_workflow_rejects_bad_extension_encoding_json_and_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge, window = self.bridge(None)
            with patch.dict(sys.modules, {'webview': Mock(OPEN_DIALOG='open')}):
                for name, content in [('wrong.txt', b'{}'), ('encoding.json', b'\xff'), ('syntax.json', b'{')]:
                    with self.subTest(name=name):
                        target = Path(directory) / name
                        target.write_bytes(content)
                        window.create_file_dialog.return_value = [str(target)]
                        self.assertFalse(bridge.select_agent_path('workflow')['ok'])
                window.create_file_dialog.return_value = [directory]
                self.assertFalse(bridge.select_agent_path('workflow')['ok'])

    def test_comfyui_normalizes_only_the_explicitly_selected_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / 'selected'
            selected.mkdir()
            normalized = selected / 'ComfyUI'
            bridge, window = self.bridge([str(selected)])
            service = Mock(normalize_root=Mock(return_value=normalized))
            with patch.dict(sys.modules, {'webview': Mock(FOLDER_DIALOG='folder'), 'app.creation_custom': service}):
                self.assertEqual(bridge.select_agent_path('comfyui'), {'ok': True, 'path': str(normalized)})
            service.normalize_root.assert_called_once_with(selected.resolve())
            self.assertFalse(normalized.exists())
