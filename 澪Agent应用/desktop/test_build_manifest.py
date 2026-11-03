from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from desktop.build_manifest import ANIF_REQUIRED_ARTIFACTS, finalize_manifest, repository_state


def anif_fixture_files() -> dict[str, bytes]:
    prefix = "_internal/agent_scripts/"
    files = {prefix + relative: ("synthetic " + name).encode()
             for name, relative in ANIF_REQUIRED_ARTIFACTS.items()}
    wheel_path = "deps/wheels/jieba_fast-0.53-cp310-cp310-win_amd64.whl"
    wheel = files[prefix + wheel_path]
    license_path = "deps/wheels/jieba-fast-LICENSE.txt"
    record = {"path": wheel_path.removeprefix("deps/"), "bytes": len(wheel),
              "sha256": hashlib.sha256(wheel).hexdigest()}
    files[prefix + "deps/aniflive-sources.json"] = json.dumps({
        "schema": 1, "bundled_wheels": [record],
        "torch_wheels": [{"url": "https://example.invalid/torch.whl", "sha256": "0" * 64}],
        "tensorrt_wheels": [{"url": "https://example.invalid/tensorrt.whl", "sha256": "1" * 64}],
    }).encode()
    files[prefix + "deps/wheels/jieba-fast-provenance.json"] = json.dumps({
        "component": "jieba-fast", "version": "0.53",
        "artifact": dict(record, path=Path(wheel_path).name),
        "license_notice": {"path": "jieba-fast-LICENSE.txt",
                           "sha256": hashlib.sha256(files[prefix + license_path]).hexdigest()},
    }).encode()
    return files


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
                "_internal/build_identity.json": identity.read_bytes(),
                "_internal/live2d_desktop/resources/app.asar": b"asar",
                "_internal/agent_frontend/index.html": b"html",
                "_internal/agent_frontend/assets/index-test.js": b"js",
                "_internal/agent_frontend/assets/index-test.css": b"css",
                "_internal/agent_frontend/assets/SettingsPage-test.js": b"lazy-js",
                "_internal/app/main.pyc": b"backend-bytecode",
                "_internal/default_voice/mio_v2_00.wav": b"reference",
                "_internal/agent_scripts/deps/install-gpt-sovits.ps1": b"installer",
                "_internal/agent_scripts/deps/install-mio-voice-package.py": b"package-installer",
            }
            files.update(anif_fixture_files())
            for relative, content in files.items():
                target = release / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            payload = finalize_manifest(identity, release)

            manifest = json.loads((release / "构建清单.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["build_id"], "mio-test-build")
        self.assertEqual(len(manifest["artifacts"]), 11 + len(ANIF_REQUIRED_ARTIFACTS))
        paths = {item["path"] for item in manifest["artifacts"]}
        self.assertIn("_internal/agent_frontend/assets/SettingsPage-test.js", paths)
        self.assertIn("_internal/app/main.pyc", paths)
        self.assertFalse(any("MioVoice" in item["path"] for item in manifest["artifacts"]))
        executable = next(item for item in manifest["artifacts"] if item["name"] == "windows_executable")
        self.assertEqual(executable["sha256"], hashlib.sha256(b"exe").hexdigest().upper())
        for name, relative in ANIF_REQUIRED_ARTIFACTS.items():
            artifact = next(item for item in manifest["artifacts"] if item["name"] == name)
            self.assertEqual(artifact["path"], "_internal/agent_scripts/" + relative)
            self.assertEqual(artifact["sha256"], hashlib.sha256(files[artifact["path"]]).hexdigest().upper())

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
                "_internal/build_identity.json": identity.read_bytes(),
                "_internal/live2d_desktop/resources/app.asar": b"asar",
                "_internal/agent_frontend/index.html": b"html",
                "_internal/agent_frontend/assets/index-test.js": b"js",
                "_internal/agent_frontend/assets/index-test.css": b"css",
                "_internal/agent_scripts/deps/install-gpt-sovits.ps1": b"installer",
                "_internal/agent_scripts/deps/install-mio-voice-package.py": b"package-installer",
            }
            files.update(anif_fixture_files())
            for relative, content in files.items():
                target = release / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            payload = finalize_manifest(identity, release)

        self.assertEqual(len(payload["artifacts"]), 8 + len(ANIF_REQUIRED_ARTIFACTS))
        self.assertFalse(any(item["name"] == "mio_default_voice_reference" for item in payload["artifacts"]))

    def test_finalize_rejects_new_identity_for_an_old_executable_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = root / "identity.json"
            identity.write_text(json.dumps({"schema_version": 1, "build_id": "new"}), encoding="utf-8")
            embedded = root / "release/_internal/build_identity.json"
            embedded.parent.mkdir(parents=True)
            embedded.write_text(json.dumps({"schema_version": 1, "build_id": "old"}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "内嵌身份不一致"):
                finalize_manifest(identity, root / "release")
            self.assertFalse((root / "release/构建清单.json").exists())


    def test_capability_frontend_cannot_finalize_without_matching_backend_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = root / "identity.json"
            identity.write_text(json.dumps({"schema_version": 1, "build_id": "new"}), encoding="utf-8")
            embedded = root / "release/_internal/build_identity.json"
            embedded.parent.mkdir(parents=True)
            embedded.write_bytes(identity.read_bytes())
            asset = root / "release/_internal/agent_frontend/assets/SettingsPage-test.js"
            asset.parent.mkdir(parents=True)
            asset.write_text("fetch('/api/capabilities')", encoding="utf-8")
            (asset.parent / "index-test.js").write_text("js", encoding="utf-8")
            (asset.parent / "index-test.css").write_text("css", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "后端未完整打包"):
                finalize_manifest(identity, root / "release")
            self.assertFalse((root / "release/构建清单.json").exists())

    def test_backend_files_cannot_hide_a_missing_frontend_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = root / "identity.json"
            identity.write_text(json.dumps({"schema_version": 1, "build_id": "new"}), encoding="utf-8")
            embedded = root / "release/_internal/build_identity.json"
            embedded.parent.mkdir(parents=True)
            embedded.write_bytes(identity.read_bytes())
            backend = embedded.parent / "app"
            backend.mkdir()
            for number in range(10):
                (backend / f"module{number}.pyc").write_bytes(b"bytecode")
            with self.assertRaisesRegex(RuntimeError, "前端入口 JS 或 CSS"):
                finalize_manifest(identity, root / "release")


class AnifPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.identity = self.root / "identity.json"
        identity = {"schema_version": 1, "build_id": "anif-build", "sources": {}}
        self.identity.write_text(json.dumps(identity), encoding="utf-8")
        files = {
            "Mio.exe": b"executable",
            "_internal/MioUpdater.exe": b"updater",
            "_internal/desktop/update_channel.json": b"{}",
            "_internal/build_identity.json": self.identity.read_bytes(),
            "_internal/live2d_desktop/resources/app.asar": b"asar",
            "_internal/agent_frontend/index.html": b"html",
            "_internal/agent_frontend/assets/index-test.js": b"js",
            "_internal/agent_frontend/assets/index-test.css": b"css",
            "_internal/agent_scripts/deps/install-gpt-sovits.ps1": b"installer",
            "_internal/agent_scripts/deps/install-mio-voice-package.py": b"voice-installer",
        }
        files.update(anif_fixture_files())
        for relative, content in files.items():
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.scripts = self.release / "_internal/agent_scripts"

    def test_missing_worker_or_wheel_prevents_finalization(self):
        for name in ("anif_setup_worker", "anif_jieba_wheel"):
            with self.subTest(name=name):
                path = self.scripts / ANIF_REQUIRED_ARTIFACTS[name]
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, "Anif 安装链文件缺失"):
                    finalize_manifest(self.identity, self.release)
                self.assertFalse((self.release / "构建清单.json").exists())
                path.write_bytes(original)

    def test_tampered_wheel_or_license_cannot_be_blessed_with_new_manifest(self):
        for name in ("anif_jieba_wheel", "anif_jieba_license"):
            with self.subTest(name=name):
                path = self.scripts / ANIF_REQUIRED_ARTIFACTS[name]
                original = path.read_bytes()
                path.write_bytes(b"tampered" + original)
                with self.assertRaisesRegex(RuntimeError, "身份校验失败"):
                    finalize_manifest(self.identity, self.release)
                path.write_bytes(original)

    def test_bundled_wheel_paths_must_be_fixed_canonical_relative_paths(self):
        lock_path = self.scripts / "deps/aniflive-sources.json"
        original = json.loads(lock_path.read_text())
        for value in ("../outside.whl", "C:/outside.whl", "/outside.whl",
                      "wheels\\jieba.whl", "wheels/file:stream.whl", "./wheels/jieba.whl",
                      "wheels//jieba.whl", "wheels/different.whl"):
            with self.subTest(path=value):
                lock = json.loads(json.dumps(original))
                lock["bundled_wheels"][0]["path"] = value
                lock_path.write_text(json.dumps(lock))
                with self.assertRaises(RuntimeError):
                    finalize_manifest(self.identity, self.release)

    def test_source_lock_and_provenance_must_agree_about_wheel_hash(self):
        provenance_path = self.scripts / "deps/wheels/jieba-fast-provenance.json"
        payload = json.loads(provenance_path.read_text())
        payload["artifact"]["sha256"] = "f" * 64
        provenance_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(RuntimeError, "身份校验失败"):
            finalize_manifest(self.identity, self.release)

    def test_existing_candidate_verifier_rejects_tampered_anif_worker(self):
        from desktop.updates.local_candidate import LOCAL_KIND, PRODUCT_ID, inventory, sha256, verify_candidate
        from desktop.updates.manifest import UpdateError
        build = finalize_manifest(self.identity, self.release)
        worker_path = self.scripts / ANIF_REQUIRED_ARTIFACTS["anif_setup_worker"]
        worker_path.write_bytes(b"tampered synthetic worker")
        # Even a fresh outer inventory cannot hide disagreement with the
        # immutable build's recorded Anif artifact hash.
        candidate = dict(kind=LOCAL_KIND, product_id=PRODUCT_ID, build_id=build["build_id"],
                         source_job_id="synthetic-job", base_build_id="old-build", sources={},
                         checks=[{"ok": True}], preview={"verified": True}, files=inventory(self.release))
        path = self.root / "candidate.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaisesRegex(UpdateError, "构建制品校验失败"):
            verify_candidate(self.release, path, sha256(path))
