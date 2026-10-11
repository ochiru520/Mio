import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import launcher


class AgentPathPickerTests(unittest.TestCase):
    def test_agent_conversation_window_validates_id_and_uses_native_close(self):
        bridge=launcher.DesktopBridge('agent')
        window=MagicMock(); webview=Mock(); webview.create_window.return_value=window
        with patch('app.conversation_state_service.require') as require, patch.dict(sys.modules,{'webview':webview}):
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
