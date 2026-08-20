from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


creator = load_module("mio_voice_package_creator", ROOT / "desktop" / "create_mio_voice_package.py")
installer = load_module("mio_voice_package_installer", ROOT / "scripts" / "deps" / "install-mio-voice-package.py")


class MioVoicePackageTests(unittest.TestCase):
    def test_create_and_install_round_trip_with_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            for relative in creator.INCLUDE:
                target = source / relative
                if Path(relative).suffix:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((relative + "-payload").encode("utf-8"))
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "sample.bin").write_bytes((relative + "-payload").encode("utf-8"))
            package = root / "Mio-voice.zip"
            metadata = creator.create(source, package)
            destination = root / "installed"
            result = installer.install(package, destination)

            self.assertEqual(metadata["file_count"], len(creator.INCLUDE))
            self.assertEqual(result["package_sha256"], metadata["sha256"])
            self.assertTrue((destination / "models" / "genie" / "mio-v1" / "sample.bin").is_file())
            self.assertTrue((destination / ".mio-native-voice-package.json").is_file())

    def test_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            package = root / "unsafe.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    '{"format":"mio-native-voice-package","files":['
                    '{"path":"payload/../escape.bin","size":1,'
                    '"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}'
                )
                archive.writestr("payload/../escape.bin", b"x")
            with self.assertRaisesRegex(ValueError, "不安全路径"):
                installer.install(package, root / "installed")


if __name__ == "__main__":
    unittest.main()
