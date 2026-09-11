import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import launcher


class AgentPathPickerTests(unittest.TestCase):
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
