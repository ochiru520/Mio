"""Mio-owned HTTP bridge for the official IndexTTS2 Python API.

Run with the IndexTTS repository's Python environment; no Mio/torch deps added.
See indextts2_adapter.md. Does not download or install anything itself.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import wave

PROTOCOL = "mio-indextts2-v1"
MAX_BODY = 57 * 1024 * 1024
MAX_REFERENCE = 40 * 1024 * 1024
MAX_AUDIO = 64 * 1024 * 1024
EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}


def synthesize(model, payload: dict) -> bytes:
    if payload.get("protocol") != PROTOCOL:
        raise ValueError("请求协议不匹配，请更新 Mio 与适配脚本。")
    text = str(payload.get("text") or "").strip()
    suffix = Path(str(payload.get("reference_name") or "")).suffix.lower()
    if not text or len(text) > 600 or suffix not in EXTENSIONS:
        raise ValueError("需要 1 到 600 字文本与支持格式的参考音频。")
    try:
        reference = base64.b64decode(payload.get("reference_audio_base64") or "", validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("参考音频编码无效。") from exc
    if not 0 < len(reference) <= MAX_REFERENCE:
        raise ValueError("参考音频不能为空或超过 40 MB。")
    with tempfile.TemporaryDirectory(prefix="mio-indextts2-") as folder:
        ref = Path(folder) / f"reference{suffix}"
        output = Path(folder) / "speech.wav"
        ref.write_bytes(reference)
        model.infer(spk_audio_prompt=str(ref), text=text, output_path=str(output), verbose=False)
        if output.stat().st_size > MAX_AUDIO:
            raise OSError("模型生成的音频超过 64 MB 限制。")
        content = output.read_bytes()
        with wave.open(BytesIO(content), "rb") as audio:
            expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            if audio.getcomptype() != "NONE" or not 0 < expected <= MAX_AUDIO or len(audio.readframes(audio.getnframes())) != expected:
                raise OSError("模型没有生成可播放的 PCM WAV。")
        return content


def handler_for(model):
    inference_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log speech text, reference paths or audio.

        def respond(self, status, content, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def error(self, status, detail):
            self.respond(status, json.dumps({"detail": detail}, ensure_ascii=False).encode())

        def do_GET(self):
            if self.path != "/health":
                self.error(404, "接口不存在。")
                return
            self.respond(200, json.dumps({"protocol": PROTOCOL, "ready": True, "engine": "indextts2"}).encode())

        def do_POST(self):
            if self.path != "/synthesize":
                self.error(404, "接口不存在。")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.error(413, "请求为空或超过参考音频大小限制。")
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self.error(415, "需要 JSON 请求。")
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("需要 JSON 对象。")
                with inference_lock:
                    content = synthesize(model, payload)
            except (ValueError, TypeError) as exc:
                self.error(400, str(exc))
                return
            except Exception as exc:
                # Exception text from ML libraries can include private text/paths.
                print(f"IndexTTS2 合成失败: {type(exc).__name__}", file=sys.stderr, flush=True)
                self.error(500, f"模型推理失败（{type(exc).__name__}），请检查模型完整性、参考音频解码、设备与显存。")
                return
            self.respond(200, content, "audio/wav")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="IndexTTS source checkout")
    parser.add_argument("--model-dir", required=True, type=Path, help="Existing IndexTTS-2 checkpoint directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11996)
    parser.add_argument("--fp16", action="store_true")
    args = parser.parse_args()
    repo, model_dir = args.repo.resolve(), args.model_dir.resolve()
    if not (repo / "indextts/infer_v2.py").is_file() or not (model_dir / "config.yaml").is_file():
        parser.error("需要已安装的官方 IndexTTS2 源码与包含 config.yaml 的 IndexTTS-2 模型目录。")
    # Avoid implicit Hugging Face fetches; provision all weights separately.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from indextts.infer_v2 import IndexTTS2
    model = IndexTTS2(cfg_path=str(model_dir / "config.yaml"), model_dir=str(model_dir),
                      use_fp16=args.fp16, use_cuda_kernel=False, use_deepspeed=False)
    # Inference lock serializes GPU work; health requests stay responsive.
    server = HTTPServer((args.host, args.port), handler_for(model))
    print(f"Mio IndexTTS2 适配服务已就绪：http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
