from __future__ import annotations

import re
import unittest
from pathlib import Path


INSTALLER_SCRIPT = Path(__file__).with_name("installer.iss")


def _code_block(source: str, start: str, end: str) -> str:
    match = re.search(
        rf"{re.escape(start)}(?P<body>.*?)(?={re.escape(end)})",
        source,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        raise AssertionError(f"installer block not found: {start}")
    return match.group("body")


class InstallerScriptTests(unittest.TestCase):
    def test_release_uses_mio_directory_and_does_not_reuse_legacy_install_path(self) -> None:
        source = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")

        self.assertIn("UsePreviousAppDir=no", source)
        self.assertIn('Source: "..\\release\\Mio\\*"', source)
        self.assertNotIn('Source: "..\\release\\MioAgent\\*"', source)

    def test_fresh_data_choice_is_first_and_selected_by_default(self) -> None:
        source = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
        initialize_wizard = _code_block(source, "procedure InitializeWizard;", "function NextButtonClick")

        fresh = initialize_wizard.index("DataModePage.Add('创建全新独立数据")
        existing = initialize_wizard.index("DataModePage.Add('沿用原有数据")
        self.assertLess(fresh, existing)
        self.assertIn("DataModePage.SelectedValueIndex := 0", initialize_wizard)

    def test_initial_data_dir_does_not_expand_app_before_setup_initializes_it(self) -> None:
        source = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
        initial_data_dir = _code_block(
            source,
            "function InitialDataDir: String;",
            "procedure InitializeWizard;",
        )

        self.assertNotIn("ExpandConstant('{app}", initial_data_dir)
        self.assertIn("WizardDirValue", initial_data_dir)
        self.assertIn("ExpandConstant('{localappdata}\\Mio')", initial_data_dir)

    def test_installed_data_pointer_is_written_only_after_installation(self) -> None:
        source = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
        initialize_wizard = _code_block(
            source,
            "procedure InitializeWizard;",
            "function NextButtonClick",
        )
        post_install = _code_block(
            source,
            "procedure CurStepChanged",
            "end;",
        )

        self.assertNotIn("ExpandConstant('{app}", initialize_wizard)
        self.assertIn("if CurStep = ssPostInstall", post_install)
        self.assertIn("ExpandConstant('{app}\\数据目录.txt')", post_install)
        self.assertIn("安装来源目录.txt", post_install)
        self.assertIn("ExpandConstant('{src}')", post_install)


if __name__ == "__main__":
    unittest.main()
