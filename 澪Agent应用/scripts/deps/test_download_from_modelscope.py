from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("download-from-modelscope.py")
SPEC = importlib.util.spec_from_file_location("mio_modelscope_downloader", SCRIPT)
assert SPEC and SPEC.loader
downloader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloader)


class FakeResponse:
    status = 206
    headers = {"Content-Length": "3", "Content-Range": "bytes 3-5/6"}

    def __init__(self) -> None:
        self._chunks = [b"def", b""]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0)


class ModelScopeDownloaderTests(unittest.TestCase):
    def test_partial_file_resumes_with_http_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "model.bin"
            target.with_suffix(".bin.part").write_bytes(b"abc")
            captured = {}

            def fake_open(request, timeout):
                del timeout
                captured["range"] = request.headers.get("Range")
                return FakeResponse()

            with mock.patch.object(downloader.urllib.request, "urlopen", side_effect=fake_open):
                size = downloader.download(
                    "https://modelscope.cn/model.bin",
                    str(target),
                    file_name="model.bin",
                    file_index=1,
                    file_count=1,
                    completed_bytes=0,
                    grand_total=6,
                    expected_file_bytes=6,
                )

            self.assertEqual(captured["range"], "bytes=3-")
            self.assertEqual(size, 6)
            self.assertEqual(target.read_bytes(), b"abcdef")


if __name__ == "__main__":
    unittest.main()
