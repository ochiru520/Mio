from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from desktop.build_manifest import finalize_manifest, repository_state


class BuildManifestTests(unittest.TestCase):
    def test_windows_build_script_is_ascii_for_powershell_51(self) -> None:
        build_script = Path(__file__).resolve().parents[1] / "构建Windows应用.ps1"

        build_script.read_text(encoding="ascii")

    def test_finalize_records_executable_live2d_and_frontend_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = root / "identity.json"
            identity.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "build_id": "mio-test-build",
                        "app_version": "0.6.2.0",
                        "built_at_utc": "2026-08-15T00:00:00Z",
                        "sources": {},
                    }
                ),
                encoding="utf-8",
            )
            release = root / "release"
            files = {
                "Mio.exe": b"exe",
                "_internal/live2d_desktop/resources/app.asar": b"asar",
                "_internal/agent_frontend/index.html": b"html",
                "_internal/agent_frontend/assets/index-test.js": b"js",
                "_internal/agent_frontend/assets/index-test.css": b"css",
                "_internal/default_voice/mio_v2_00.wav": b"reference",
                "_internal/agent_scripts/deps/install-gpt-sovits.ps1": b"installer",
                "_internal/agent_scripts/deps/install-mio-voice-package.py": b"package-installer",
            }
            for relative, content in files.items():
                target = release / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            payload = finalize_manifest(identity, release)

            manifest = json.loads((release / "构建清单.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["build_id"], "mio-test-build")
        self.assertEqual(len(manifest["artifacts"]), 8)
        self.assertFalse(any("MioVoice" in item["path"] for item in manifest["artifacts"]))
        executable = next(item for item in manifest["artifacts"] if item["name"] == "windows_executable")
        self.assertEqual(executable["sha256"], hashlib.sha256(b"exe").hexdigest().upper())

    def test_repository_state_uses_stable_snapshot_without_git_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('public')\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("first", encoding="utf-8")

            initial = repository_state(root)
            (root / "node_modules" / "ignored.js").write_text("second", encoding="utf-8")
            ignored_change = repository_state(root)
            (root / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
            source_change = repository_state(root)

        self.assertTrue(str(initial["commit"]).startswith("source-"))
        self.assertFalse(initial["dirty"])
        self.assertEqual(initial, ignored_change)
        self.assertNotEqual(initial["dirty_hash"], source_change["dirty_hash"])

    def test_finalize_allows_public_build_without_private_voice_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = root / "identity.json"
            identity.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "build_id": "mio-public-build",
                        "app_version": "0.7.0.0",
                        "built_at_utc": "2026-08-20T00:00:00Z",
                        "sources": {},
                    }
                ),
                encoding="utf-8",
            )
            release = root / "release"
            files = {
                "Mio.exe": b"exe",
                "_internal/live2d_desktop/resources/app.asar": b"asar",
                "_internal/agent_frontend/index.html": b"html",
                "_internal/agent_frontend/assets/index-test.js": b"js",
                "_internal/agent_frontend/assets/index-test.css": b"css",
                "_internal/agent_scripts/deps/install-gpt-sovits.ps1": b"installer",
                "_internal/agent_scripts/deps/install-mio-voice-package.py": b"package-installer",
            }
            for relative, content in files.items():
                target = release / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            payload = finalize_manifest(identity, release)

        self.assertEqual(len(payload["artifacts"]), 7)
        self.assertFalse(any(item["name"] == "mio_default_voice_reference" for item in payload["artifacts"]))
