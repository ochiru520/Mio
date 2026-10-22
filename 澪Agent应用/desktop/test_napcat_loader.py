"""Exercise the real PS5 loader preparation and Node's Electron-style entry lookup."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "scripts" / "napcat-loader.ps1"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell filesystem behavior")
class NapCatLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mio-napcat-loader-")
        self.root = Path(self.temp.name)
        self.shell = self.root / "Shell 中文 # % ' 空格"
        self.shell.mkdir()
        self.main = self.shell / "napcat.mjs"
        self.main.write_text("console.log('MIO_NAPCAT_IMPORT_OK');\n", encoding="utf-8")
        self.patch = self.shell / "qqnt.json"
        self.patch.write_text(json.dumps({"name": "qq-chat", "main": "./loadNapCat.js",
                                         "version": "9.9.22", "custom": {"keep": True}}), encoding="utf-8")
        self.qq = self.root / "QQ 中文 # ' %" / "QQ.exe"
        self.qq.parent.mkdir()
        self.qq.write_bytes(b"test-only-not-an-executable")
        self.app = self.qq.parent / "versions" / "9.9.29-47354" / "resources" / "app"
        self.app.mkdir(parents=True)
        self.package = self.app / "package.json"
        self.package.write_text('{"name":"qq-chat","main":"./app_launcher/index.js"}', encoding="utf-8")
        self.originals = {p: p.read_bytes() for p in (self.patch, self.package, self.qq, self.main)}

    def tearDown(self):
        self.temp.cleanup()

    def run_helper(self, *, before="", expect_ok=True):
        driver = self.root / "driver.ps1"
        quote = lambda s: "'" + str(s).replace("'", "''") + "'"
        driver.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f". {quote(HELPER)}\n"
            + before + "\n"
            f"Initialize-MioNapCatLoader -ShellRoot {quote(self.shell)} -QQPath {quote(self.qq)} | ConvertTo-Json -Depth 5\n",
            encoding="utf-8-sig",
        )
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def assert_originals_preserved(self):
        for path, content in self.originals.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the real loader")
    def test_electron_join_entry_imports_special_character_path_without_environment(self):
        prepared = self.run_helper()
        patch = json.loads(Path(prepared["Patch"]).read_text(encoding="utf-8"))
        self.assertEqual(patch["custom"], {"keep": True})
        self.assertEqual(patch["version"], "9.9.22")
        self.assertEqual(patch["main"], "./mio-napcat-loader.cjs")
        self.assertEqual(len(prepared["AppRoots"]), 1)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("NAPCAT_")}
        # Same join(packagePath, packageJson.main) as Electron's main process.
        result = subprocess.run(["node", "-e", "require(require('node:path').join(process.argv[1], JSON.parse(require('node:fs').readFileSync(process.argv[2], 'utf8')).main))",
                                 str(self.app), prepared["Patch"]], cwd=self.root, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MIO_NAPCAT_IMPORT_OK", result.stdout)
        for path in (Path(prepared["Load"]), Path(prepared["Patch"]), self.app / "mio-napcat-loader.cjs"):
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assert_originals_preserved()

    def test_repeat_is_idempotent_and_new_qq_version_gets_entry(self):
        self.run_helper()
        tracked = [self.shell / "qqnt.mio.json", self.shell / "mio-napcat-loader.cjs", self.app / "mio-napcat-loader.cjs"]
        before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in tracked}
        self.run_helper()
        self.assertEqual(before, {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in tracked})
        new_app = self.app.parents[2] / "9.9.30-48000" / "resources" / "app"
        new_app.mkdir(parents=True)
        (new_app / "package.json").write_bytes(self.package.read_bytes())
        self.run_helper()
        self.assertTrue((new_app / "mio-napcat-loader.cjs").is_file())
        self.assert_originals_preserved()

    def test_direct_layout_and_unrelated_package(self):
        direct = self.qq.parent / "resources" / "app"
        direct.mkdir(parents=True)
        (direct / "package.json").write_bytes(self.package.read_bytes())
        self.package.write_text('{"name":"unrelated-app"}', encoding="utf-8")
        prepared = self.run_helper()
        self.assertEqual(prepared["AppRoots"], [str(direct)])
        self.assertFalse((self.app / "mio-napcat-loader.cjs").exists())

    def test_missing_or_invalid_patch_fails_before_writes(self):
        for payload in (None, "not json", "null", '[]', '{"name":"qq-chat"}'):
            with self.subTest(payload=payload):
                if payload is None:
                    self.patch.unlink(missing_ok=True)
                else:
                    self.patch.write_text(payload, encoding="utf-8")
                self.run_helper(expect_ok=False)
                self.assertFalse((self.shell / "mio-napcat-loader.cjs").exists())
                self.assertFalse((self.app / "mio-napcat-loader.cjs").exists())

    def test_unknown_loader_is_not_overwritten(self):
        target = self.app / "mio-napcat-loader.cjs"
        target.write_text("user owned content", encoding="utf-8")
        error = self.run_helper(expect_ok=False)
        self.assertIn("非 Mio 受管", error)
        self.assertEqual(target.read_text(), "user owned content")
        self.assertFalse((self.shell / "mio-napcat-loader.cjs").exists())
        self.assert_originals_preserved()

    def test_missing_qq_layout_or_main_refuses_start(self):
        self.main.unlink()
        self.assertIn("所需文件不存在", self.run_helper(expect_ok=False))
        self.main.write_bytes(self.originals[self.main])
        self.package.unlink()
        self.assertIn("没有找到有效", self.run_helper(expect_ok=False))
        self.assertFalse((self.shell / "qqnt.mio.json").exists())

    def test_junction_is_rejected_without_writing_outside_installation(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "app").mkdir()
        (outside / "app" / "package.json").write_bytes(self.package.read_bytes())
        linked = self.qq.parent / "resources"
        # Point the direct resources layout outside QQ. No target deletion is used.
        driver = self.root / "junction.ps1"
        q = lambda p: "'" + str(p).replace("'", "''") + "'"
        driver.write_text(f"New-Item -ItemType Junction -Path {q(linked)} -Target {q(outside)} | Out-Null", encoding="utf-8-sig")
        subprocess.run(["powershell.exe", "-NoProfile", "-File", str(driver)], check=True, capture_output=True)
        try:
            self.assertIn("目录联接", self.run_helper(expect_ok=False))
            self.assertFalse((outside / "app" / "mio-napcat-loader.cjs").exists())
        finally:
            os.rmdir(linked)

    def test_locked_destination_rolls_back_previous_change(self):
        self.run_helper()
        loader = self.app / "mio-napcat-loader.cjs"
        shell_loader = self.shell / "mio-napcat-loader.cjs"
        old_shell = shell_loader.read_bytes()
        old_app = loader.read_bytes()
        # Force a changed import path while retaining a valid main.
        new_shell = self.root / "new-shell"
        new_shell.mkdir()
        for source in (self.main, self.patch):
            (new_shell / source.name).write_bytes(source.read_bytes())
        self.shell = new_shell
        q = "'" + str(loader).replace("'", "''") + "'"
        error = self.run_helper(before=f"$lock = [IO.File]::Open({q}, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)", expect_ok=False)
        self.assertIn("已撤回本次修改", error)
        self.assertFalse((new_shell / "mio-napcat-loader.cjs").exists())
        self.assertFalse((new_shell / "qqnt.mio.json").exists())
        self.assertEqual(shell_loader.read_bytes(), old_shell)
        self.assertEqual(loader.read_bytes(), old_app)
        self.assertEqual(list(new_shell.glob("*.tmp")), [])

    def test_control_script_passes_managed_patch_to_launcher_and_keeps_qr_mode(self):
        for name in ("NapCatWinBootMain.exe", "NapCatWinBootHook.dll"):
            (self.shell / name).write_bytes(b"test-only-do-not-execute")
        control = HELPER.with_name("napcat-control.ps1")
        q = lambda p: "'" + str(p).replace("'", "''") + "'"
        for forced in (False, True):
            with self.subTest(forced=forced):
                marker = self.root / "launch.json"
                driver = self.root / "control-driver.ps1"
                driver.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    "function Get-CimInstance { @() }\n"
                    "function Get-NetTCPConnection { @() }\n"
                    "function Start-Sleep {}\n"
                    "function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)\n"
                    "@{File=$FilePath; Args=$ArgumentList; Cwd=$WorkingDirectory; Patch=$env:NAPCAT_PATCH_PACKAGE; Load=$env:NAPCAT_LOAD_PATH} "
                    f"| ConvertTo-Json | Set-Content -LiteralPath {q(marker)} -Encoding UTF8\n"
                    "[pscustomobject]@{HasExited=$false}\n}\n"
                    f"& {q(control)} -Action start\n", encoding="utf-8-sig")
                env = os.environ.copy()
                env.update(MIO_NAPCAT_DIR=str(self.shell), MIO_NAPCAT_ACCOUNT="12345678", MIO_QQ_EXE=str(self.qq),
                           MIO_RUNTIME_ROOT=str(self.root), MIO_NAPCAT_FORCE_QR="1" if forced else "0",
                           NAPCAT_PATCH_PACKAGE="stale-value-that-must-not-be-used")
                completed = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                launch = json.loads(marker.read_text(encoding="utf-8-sig"))
                self.assertEqual(launch["File"], str(self.shell / "NapCatWinBootMain.exe"))
                self.assertEqual(launch["Patch"], str(self.shell / "qqnt.mio.json"))
                self.assertEqual(launch["Load"], str(self.shell / "mio-napcat-loader.cjs"))
                self.assertEqual(launch["Cwd"], str(self.shell))
                self.assertEqual("-q" in launch["Args"], not forced)
                self.assertTrue((self.app / "mio-napcat-loader.cjs").exists())
        self.assert_originals_preserved()


@unittest.skipUnless(os.name == "nt", "Windows hotfix installer")
class NapCatHotfixInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mio-napcat-hotfix-")
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        for name in ("napcat-control.ps1", "napcat-loader.ps1", "repair-napcat-installation.ps1"):
            shutil.copyfile(HELPER.with_name(name), self.bundle / name)
        self.hashes = {name: hashlib.sha256((self.bundle / name).read_bytes()).hexdigest().upper()
                       for name in ("napcat-control.ps1", "napcat-loader.ps1")}
        (self.bundle / "SHA256.json").write_text(json.dumps(self.hashes), encoding="utf-8-sig")
        self.install = self.root / "Mio 中文"
        self.scripts = self.install / "_internal" / "agent_scripts"
        self.scripts.mkdir(parents=True)
        (self.install / "Mio.exe").write_bytes(b"test only")
        self.old = b"# original control script\r\n"
        (self.scripts / "napcat-control.ps1").write_bytes(self.old)

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, restore=False, expect_ok=True, running=False, lock_loader=False):
        q = lambda p: "'" + str(p).replace("'", "''") + "'"
        driver = self.root / "install-driver.ps1"
        process_mock = (f"[pscustomobject]@{{ExecutablePath={q(self.install / 'Mio.exe')};Name='Mio.exe'}}"
                        if running else "@()")
        lock = (f"$lock = [IO.File]::Open({q(self.scripts / 'napcat-loader.ps1')}, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)\n"
                if lock_loader else "")
        driver.write_text(f"function Get-CimInstance {{ {process_mock} }}\n" + lock +
                          f"& {q(self.bundle / 'repair-napcat-installation.ps1')} -MioDirectory {q(self.install)}"
                          + (" -Restore" if restore else "") + "\nexit $LASTEXITCODE\n", encoding="utf-8-sig")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_install_repeat_restore_and_backup_bytes(self):
        self.run_installer()
        for name, digest in self.hashes.items():
            self.assertEqual(hashlib.sha256((self.scripts / name).read_bytes()).hexdigest().upper(), digest)
        backups = list((self.install / "备份" / "QQ加载修复").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "napcat-control.ps1").read_bytes(), self.old)
        self.assertIn("无需重复覆盖", self.run_installer())
        self.assertEqual(list((self.install / "备份" / "QQ加载修复").iterdir()), backups)
        self.run_installer(restore=True)
        self.assertEqual((self.scripts / "napcat-control.ps1").read_bytes(), self.old)
        self.assertFalse((self.scripts / "napcat-loader.ps1").exists())

    def test_corrupt_payload_or_running_mio_refuses_installation(self):
        self.assertIn("仍在运行", self.run_installer(running=True, expect_ok=False))
        with (self.bundle / "napcat-loader.ps1").open("ab") as output:
            output.write(b"\n# corruption\n")
        self.assertIn("校验失败", self.run_installer(expect_ok=False))
        self.assertEqual((self.scripts / "napcat-control.ps1").read_bytes(), self.old)
        self.assertFalse((self.install / "备份").exists())

    def test_restore_preserves_later_program_changes(self):
        self.run_installer()
        changed = b"# newer release\n"
        (self.scripts / "napcat-control.ps1").write_bytes(changed)
        self.assertIn("已有变化", self.run_installer(restore=True, expect_ok=False))
        self.assertEqual((self.scripts / "napcat-control.ps1").read_bytes(), changed)

    def test_restore_skips_unfinished_install_and_rolls_back_on_locked_file(self):
        self.run_installer()
        unfinished = self.install / "备份" / "QQ加载修复" / "99999999-unfinished"
        unfinished.mkdir()
        (unfinished / "receipt.json").write_text('{"state":"prepared"}', encoding="utf-8")
        before = {name: (self.scripts / name).read_bytes() for name in self.hashes}
        self.assertIn("已回滚到撤销前", self.run_installer(restore=True, lock_loader=True, expect_ok=False))
        for name, contents in before.items():
            self.assertEqual((self.scripts / name).read_bytes(), contents)
        self.run_installer(restore=True)
        self.assertEqual((self.scripts / "napcat-control.ps1").read_bytes(), self.old)
        self.assertFalse((self.scripts / "napcat-loader.ps1").exists())


if __name__ == "__main__":
    unittest.main()
