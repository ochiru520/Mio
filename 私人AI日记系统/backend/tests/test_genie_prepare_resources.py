from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "澪Agent应用"
    / "scripts"
    / "deps"
    / "prepare-genie-resources.py"
)


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("mio_prepare_genie_resources", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 Genie 资源准备脚本。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeniePrepareResourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_prepare_module()

    def test_torch_231_signature_does_not_receive_dynamo(self) -> None:
        def legacy_export(model, args, output, input_names=None):
            return None

        options = self.module.onnx_export_options(legacy_export)

        self.assertNotIn("dynamo", options)
        self.assertEqual(options["opset_version"], 17)

    def test_newer_torch_signature_explicitly_disables_dynamo(self) -> None:
        def modern_export(model, args, output, *, dynamo=True):
            return None

        options = self.module.onnx_export_options(modern_export)

        self.assertIs(options["dynamo"], False)

    def test_lightweight_installer_pins_numpy_without_pytorch_conversion(self) -> None:
        installer = SCRIPT_PATH.with_name("install-gpt-sovits.ps1").read_text(encoding="utf-8-sig")
        engine_installer = SCRIPT_PATH.with_name("install-genie-runtime.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('"numpy==1.26.4"', engine_installer)
        self.assertNotIn('"torch==2.3.1"', installer)
        self.assertIn("install-mio-voice-package.py", installer)

    def test_runtime_patch_guards_empty_pinyin_segments_from_plain_jieba(self) -> None:
        patch_script = SCRIPT_PATH.with_name("patch-genie-runtime.py").read_text(encoding="utf-8")

        self.assertIn("and sub_finals_list[i - 1]", patch_script)
        self.assertIn("and sub_finals_list[i][0]", patch_script)

    def test_status_replace_retries_after_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mio-genie-status-") as temporary:
            status_file = Path(temporary) / "gpt_sovits.json"
            args = SimpleNamespace(status_file=str(status_file), dependency_id="gpt_sovits")
            real_replace = self.module.os.replace
            attempts = 0

            def temporarily_locked(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    error = PermissionError(5, "拒绝访问")
                    error.winerror = 5
                    raise error
                return real_replace(source, target)

            with (
                patch.object(self.module.os, "replace", side_effect=temporarily_locked),
                patch.object(self.module.time, "sleep"),
            ):
                self.module.write_status(args, "convert_hubert", 82, "继续转换")

            payload = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["stage"], "convert_hubert")
            self.assertEqual(attempts, 3)
            self.assertEqual(list(Path(temporary).glob("*.prepare.*.tmp")), [])

    def test_status_lock_never_aborts_conversion_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mio-genie-status-") as temporary:
            status_file = Path(temporary) / "gpt_sovits.json"
            args = SimpleNamespace(status_file=str(status_file), dependency_id="gpt_sovits")
            error = PermissionError(5, "拒绝访问")
            error.winerror = 5

            with (
                patch.object(self.module.os, "replace", side_effect=error),
                patch.object(self.module.time, "sleep"),
            ):
                self.module.write_status(args, "convert_hubert", 82, "继续转换")

            self.assertFalse(status_file.exists())
            self.assertEqual(list(Path(temporary).glob("*.prepare.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
