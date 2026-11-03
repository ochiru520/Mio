"""Weights are streamed as opaque data; import never executes or selects a model."""
from __future__ import annotations

import copy
import asyncio
import hashlib
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import voice_weight_import as weights
from app.routes import companion as route


class WeightImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {"default_voice_profile_id": "mio", "voice_engine": "gpt_sovits", "private": "preserved",
                       "voice_profiles": {"mio": {"engine": "gpt_sovits", "name": "original"},
                                          "custom": {"engine": "gpt_sovits", "name": "custom", "gpt_sovits_gpt_weights": "old.ckpt"}}}
        self.saved = []
        self.disk = patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3))
        self.disk.start()

    def tearDown(self):
        self.disk.stop()
        self.temporary.cleanup()

    def load(self):
        return copy.deepcopy(self.config)

    def save(self, patch):
        self.saved.append(copy.deepcopy(patch))
        self.config.update(copy.deepcopy(patch))

    def upload(self, **changes):
        arguments = dict(profile_id="custom", kind="gpt", filename="my-model.ckpt", root=self.root,
                         load_config=self.load, save_config=self.save)
        arguments.update(changes)
        return weights.WeightUpload(**arguments)

    def test_streaming_import_preserves_other_profiles_and_default_without_loading_weights(self):
        before = copy.deepcopy(self.config)
        upload = self.upload()
        try:
            for chunk in (b"not a", b" pickle ", b"just opaque data"):
                upload.write(chunk)
            result = upload.commit()
        finally:
            upload.close()
        payload = b"not a pickle just opaque data"
        self.assertEqual(Path(result["path"]).read_bytes(), payload)
        self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["size_bytes"], len(payload))
        self.assertEqual(result["validation"], "file_saved_not_model_loaded")
        self.assertEqual(self.config["default_voice_profile_id"], before["default_voice_profile_id"])
        self.assertEqual(self.config["private"], "preserved")
        self.assertEqual(self.config["voice_profiles"]["mio"], before["voice_profiles"]["mio"])
        self.assertEqual(set(self.saved[0]), {"voice_profiles"})
        self.assertEqual(list(self.root.rglob("*.uploading")), [])

    def test_second_file_keeps_the_previous_valid_model_file(self):
        old = self.root / "old.ckpt"
        old.write_bytes(b"old model")
        self.config["voice_profiles"]["custom"]["gpt_sovits_gpt_weights"] = str(old)
        upload = self.upload(kind="sovits", filename="my.pth")
        try:
            upload.write(b"new sovits")
            upload.commit()
        finally:
            upload.close()
        self.assertEqual(old.read_bytes(), b"old model")
        self.assertEqual(self.config["voice_profiles"]["custom"]["gpt_sovits_gpt_weights"], str(old))

    def test_invalid_profile_kind_or_filename_is_rejected_before_any_file_creation(self):
        bad = [{"profile_id": "../mio"}, {"profile_id": "CON"}, {"profile_id": "mio."}, {"profile_id": "missing"},
               {"kind": "unknown"}, {"filename": "../evil.ckpt"}, {"filename": "D:\\evil.ckpt"},
               {"filename": "evil.pth"}, {"filename": "evil.ckpt:stream"}, {"filename": "evil\x00.ckpt"}]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.upload(**changes)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_content_length_or_stream_limit_stops_upload_and_cleans_partial_file(self):
        with patch.object(weights, "MAX_WEIGHT_BYTES", 5):
            with self.assertRaises(weights.WeightTooLarge):
                self.upload(content_length=6)
            upload = self.upload()
            try:
                upload.write(b"1234")
                with self.assertRaises(weights.WeightTooLarge):
                    upload.write(b"56")
            finally:
                upload.close()
        self.assertFalse(self.saved)
        self.assertEqual(list(self.root.rglob("*.uploading")), [])

    def test_empty_upload_and_mid_upload_disk_exhaustion_do_not_change_config(self):
        upload = self.upload()
        try:
            with self.assertRaises(ValueError):
                upload.commit()
            with patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=weights.DISK_RESERVE_BYTES)):
                with self.assertRaises(OSError):
                    upload.write(b"1")
        finally:
            upload.close()
        self.assertFalse(self.saved)
        self.assertEqual(list(self.root.rglob("*.uploading")), [])

    def test_insufficient_disk_rejects_before_creating_partial_file(self):
        with patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=weights.DISK_RESERVE_BYTES + 2)):
            with self.assertRaises(OSError):
                self.upload(content_length=3)
        self.assertEqual(list(self.root.rglob("*.uploading")), [])

    def test_config_save_failure_rolls_back_new_file_and_keeps_old_field(self):
        upload = self.upload(save_config=lambda _: (_ for _ in ()).throw(OSError("save failed")))
        try:
            upload.write(b"some model")
            with self.assertRaises(OSError):
                upload.commit()
        finally:
            upload.close()
        self.assertEqual(self.config["voice_profiles"]["custom"]["gpt_sovits_gpt_weights"], "old.ckpt")
        self.assertFalse(upload.target.exists())
        self.assertFalse(upload.temporary.exists())

    def test_deleted_or_modified_profile_during_upload_is_not_restored_or_overwritten(self):
        for deleted in (True, False):
            with self.subTest(deleted=deleted):
                self.config["voice_profiles"]["custom"] = {"engine": "gpt_sovits", "gpt_sovits_gpt_weights": "before.ckpt"}
                upload = self.upload()
                try:
                    upload.write(b"model")
                    if deleted:
                        self.config["voice_profiles"].pop("custom")
                    else:
                        self.config["voice_profiles"]["custom"]["gpt_sovits_gpt_weights"] = "changed.ckpt"
                    with self.assertRaises(ValueError):
                        upload.commit()
                finally:
                    upload.close()
                self.assertFalse(upload.target.exists())
        self.assertFalse(self.saved)

    def test_link_or_junction_ancestor_is_rejected(self):
        original = weights.Path.lstat
        folder = self.root / "音色权重"
        folder.mkdir()
        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == folder:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(weights.Path, "lstat", lstat), self.assertRaisesRegex(ValueError, "联接"):
            self.upload()
        self.assertEqual(list(folder.iterdir()), [])


class WeightImportRouteTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, chunks, headers=()):
        async def receive():
            index = receive.index
            receive.index += 1
            return {"type": "http.request", "body": chunks[index], "more_body": index < len(chunks) - 1}
        receive.index = 0
        return Request({"type": "http", "method": "POST", "path": "/", "headers": list(headers)}, receive)

    async def test_route_streams_to_disk_and_returns_status_and_opaque_file_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"default_voice_profile_id": "mio", "voice_profiles": {"custom": {"engine": "gpt_sovits"}}}
            def save(changes):
                config.update(changes)
            request = await self.request([b"one", b"two"], [(b"content-type", b"application/octet-stream")])
            with patch.object(route, "settings", SimpleNamespace(companion_dir=Path(directory))), patch.object(route.companion_service, "load_config", side_effect=lambda: copy.deepcopy(config)), patch.object(route.companion_service, "save_config", side_effect=save), patch.object(route, "_status_payload", return_value={"pet": {"settings": {"voice_profiles": {}}}}), patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)):
                result = await route.companion_voice_weight_import("custom", "gpt", request, "model.ckpt")
            self.assertIn("pet", result)
            self.assertEqual(result["weight_import"]["size_bytes"], 6)
            self.assertEqual(Path(result["weight_import"]["path"]).read_bytes(), b"onetwo")
            self.assertEqual(config["default_voice_profile_id"], "mio")

    async def test_route_rejects_wrong_content_type_and_oversized_file(self):
        request = await self.request([b"x"], [(b"content-type", b"application/json")])
        with self.assertRaises(HTTPException) as error:
            await route.companion_voice_weight_import("custom", "gpt", request, "model.ckpt")
        self.assertEqual(error.exception.status_code, 400)
        request = await self.request([b"x"], [(b"content-type", b"application/octet-stream"), (b"content-length", b"2147483649")])
        with self.assertRaises(HTTPException) as error:
            await route.companion_voice_weight_import("custom", "gpt", request, "model.ckpt")
        self.assertEqual(error.exception.status_code, 413)

    async def test_disconnected_upload_cleans_partial_file_without_saving_profile(self):
        async def stream():
            yield b"partial weight"
            raise OSError("connection lost")
        request = SimpleNamespace(headers={"content-type": "application/octet-stream"}, stream=stream)
        with tempfile.TemporaryDirectory() as directory:
            config = {"voice_profiles": {"custom": {"engine": "gpt_sovits"}}}
            with patch.object(route, "settings", SimpleNamespace(companion_dir=Path(directory))), patch.object(route.companion_service, "load_config", return_value=config), patch.object(route.companion_service, "save_config") as save, patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)):
                with self.assertRaises(HTTPException):
                    await route.companion_voice_weight_import("custom", "gpt", request, "model.ckpt")
            self.assertEqual([path for path in Path(directory).rglob("*") if path.is_file()], [])
            save.assert_not_called()

    async def test_cancelled_route_waits_for_its_pending_write_before_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        original = weights.WeightUpload.write
        def pending_write(upload, chunk):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release writer")
            original(upload, chunk)
        with tempfile.TemporaryDirectory() as directory:
            request = await self.request([b"some bytes"], [(b"content-type", b"application/octet-stream")])
            config = {"voice_profiles": {"custom": {"engine": "gpt_sovits"}}}
            with patch.object(route, "settings", SimpleNamespace(companion_dir=Path(directory))), patch.object(route.companion_service, "load_config", return_value=config), patch.object(route.companion_service, "save_config") as save, patch.object(weights.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)), patch.object(weights.WeightUpload, "write", pending_write):
                task = asyncio.create_task(route.companion_voice_weight_import("custom", "gpt", request, "model.ckpt"))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    task.cancel()
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                    if not task.done():
                        await task
            self.assertEqual([path for path in Path(directory).rglob("*") if path.is_file()], [])
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
