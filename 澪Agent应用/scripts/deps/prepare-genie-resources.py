from __future__ import annotations

import argparse
import contextlib
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import shutil
import sys
import tempfile
import time


REQUIRED_CHARACTER_FILES = (
    "t2s_encoder_fp32.bin",
    "t2s_encoder_fp32.onnx",
    "t2s_first_stage_decoder_fp32.onnx",
    "t2s_shared_fp16.bin",
    "t2s_stage_decoder_fp32.onnx",
    "vits_fp16.bin",
    "vits_fp32.onnx",
)


def onnx_export_options(exporter) -> dict[str, object]:
    options: dict[str, object] = {
        "input_names": ["input_values"],
        "output_names": ["ssl_content"],
        "dynamic_axes": {"input_values": {1: "samples"}, "ssl_content": {2: "frames"}},
        "opset_version": 17,
        "do_constant_folding": True,
    }
    if "dynamo" in inspect.signature(exporter).parameters:
        options["dynamo"] = False
    return options


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备澪的 Genie V2 ONNX 资源。")
    parser.add_argument("--genie-data", required=True)
    parser.add_argument("--hubert-source", required=True)
    parser.add_argument("--gpt-weights", required=True)
    parser.add_argument("--sovits-weights", required=True)
    parser.add_argument("--character-output", required=True)
    parser.add_argument("--gpt-source", default="")
    parser.add_argument("--status-file", default=os.environ.get("MIO_STATUS_FILE", ""))
    parser.add_argument("--dependency-id", default=os.environ.get("MIO_DEP_ID", "gpt_sovits"))
    return parser.parse_args()


def write_status(args: argparse.Namespace, stage: str, percent: int, message: str) -> None:
    if not args.status_file:
        print(message, flush=True)
        return
    payload = {
        "id": args.dependency_id,
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "error": "",
        "done": False,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    target = Path(args.status_file).resolve()
    last_error: OSError | None = None
    for attempt, delay in enumerate((0.03, 0.08, 0.15, 0.3, 0.5), start=1):
        temporary: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                dir=target.parent,
                prefix=f"{target.name}.prepare.",
                suffix=".tmp",
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            last_error = None
            break
        except OSError as exc:
            last_error = exc
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if attempt < 5:
                time.sleep(delay)
    if last_error is not None:
        print(
            f"警告：安装进度文件暂时无法更新，将继续模型转换：{last_error}",
            file=sys.stderr,
            flush=True,
        )
    print(message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"{label}不存在或为空：{path}")
    return path


def prepare_g2p(genie_data: Path, gpt_source: Path | None) -> None:
    target = genie_data / "G2P" / "ChineseG2P"
    target.mkdir(parents=True, exist_ok=True)
    source_text = None
    source_pickle = None
    if gpt_source and gpt_source.is_dir():
        source_text = gpt_source / "GPT_SoVITS" / "text" / "opencpop-strict.txt"
        source_pickle = gpt_source / "GPT_SoVITS" / "text" / "g2pw" / "polyphonic.pickle"
    if source_text and source_text.is_file():
        shutil.copyfile(source_text, target / "opencpop-strict.txt")
    if source_pickle and source_pickle.is_file():
        shutil.copyfile(source_pickle, target / "polyphonic.pickle")
    if not (target / "opencpop-strict.txt").is_file():
        raise FileNotFoundError("GPT-SoVITS 源码中缺少中文发音字典 opencpop-strict.txt。")
    if not (target / "polyphonic.pickle").is_file():
        with (target / "polyphonic.pickle").open("wb") as stream:
            pickle.dump({}, stream, protocol=4)
    # Genie 2.0.2 会在导入时无条件检查此文件，但 V2 推理从不加载它。
    placeholder = genie_data / "speaker_encoder.onnx"
    if not placeholder.exists():
        placeholder.write_bytes(b"MIO_GENIE_V2_UNUSED\n")


def export_hubert(source: Path, output: Path) -> None:
    import numpy as np
    import onnxruntime
    import torch
    from transformers import HubertModel

    class HubertForGenie(torch.nn.Module):
        def __init__(self, model: HubertModel):
            super().__init__()
            self.model = model

        def forward(self, input_values):
            return self.model(input_values).last_hidden_state.transpose(1, 2)

    output.parent.mkdir(parents=True, exist_ok=True)
    model = HubertForGenie(HubertModel.from_pretrained(source, local_files_only=True)).eval()
    dummy = torch.zeros((1, 16000), dtype=torch.float32)
    temporary = output.with_suffix(".tmp.onnx")
    export_options = onnx_export_options(torch.onnx.export)
    with torch.inference_mode():
        torch.onnx.export(
            model,
            (dummy,),
            str(temporary),
            **export_options,
        )
    session = onnxruntime.InferenceSession(str(temporary), providers=["CPUExecutionProvider"])
    result = session.run(None, {"input_values": np.zeros((1, 16000), dtype=np.float32)})[0]
    if result.ndim != 3 or result.shape[0] != 1 or result.shape[1] != 768 or result.shape[2] <= 0:
        raise OSError(f"CNHubert ONNX 输出形状异常：{result.shape}")
    temporary.replace(output)


def character_ready(path: Path) -> bool:
    return all((path / name).is_file() and (path / name).stat().st_size > 0 for name in REQUIRED_CHARACTER_FILES)


def main() -> int:
    args = parse_args()
    genie_data = Path(args.genie_data).resolve()
    hubert_source = Path(args.hubert_source).resolve()
    gpt_weights = require_file(Path(args.gpt_weights), "GPT V2 权重")
    sovits_weights = require_file(Path(args.sovits_weights), "SoVITS V2 权重")
    character_output = Path(args.character_output).resolve()
    gpt_source = Path(args.gpt_source).resolve() if args.gpt_source else None

    genie_data.mkdir(parents=True, exist_ok=True)
    prepare_g2p(genie_data, gpt_source)
    os.environ["GENIE_DATA_DIR"] = str(genie_data)
    os.environ["HUBERT_MODEL_DIR"] = str(genie_data / "chinese-hubert-base")
    os.environ["Chinese_G2P_DIR"] = str(genie_data / "G2P" / "ChineseG2P")
    os.environ["SV_MODEL"] = str(genie_data / "speaker_encoder.onnx")

    hubert_output = genie_data / "chinese-hubert-base" / "chinese-hubert-base.onnx"
    if not hubert_output.is_file() or hubert_output.stat().st_size < 50 * 1024 * 1024:
        write_status(args, "convert_hubert", 82, "正在本机转换中文语音编码器 ONNX（只需一次）")
        export_hubert(hubert_source, hubert_output)

    if not character_ready(character_output):
        write_status(args, "convert_voice", 90, "正在本机转换 GPT-SoVITS V2 音色 ONNX（只需一次）")
        temporary = character_output.with_name(character_output.name + ".converting")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        with contextlib.redirect_stdout(sys.stderr):
            from genie_tts.Converter.Converter import convert
            convert(str(gpt_weights), str(sovits_weights), str(temporary))
        if not character_ready(temporary):
            raise OSError("Genie 转换结束，但音色 ONNX 文件不完整。")
        if character_output.exists():
            shutil.rmtree(character_output)
        temporary.replace(character_output)

    write_status(args, "validate", 98, "正在校验 Genie ONNX 输入输出与 CPU 加载")
    import onnxruntime

    hubert_session = onnxruntime.InferenceSession(str(hubert_output), providers=["CPUExecutionProvider"])
    if hubert_session.get_inputs()[0].name != "input_values":
        raise OSError("CNHubert ONNX 输入名不是 input_values。")
    with contextlib.redirect_stdout(sys.stderr):
        import genie_tts
        genie_tts.load_character("mio-install-check", character_output, "Chinese")
        genie_tts.unload_character("mio-install-check")

    marker = character_output / "mio-genie-v2.json"
    marker.write_text(json.dumps({
        "schema_version": 1,
        "runtime": "genie-tts-2.0.2",
        "model_type": "GPT-SoVITS V2",
        "gpt_sha256": sha256(gpt_weights),
        "sovits_sha256": sha256(sovits_weights),
        "hubert_sha256": sha256(hubert_output),
        "sample_rate": 32000,
        "provider": "CPUExecutionProvider",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    write_status(args, "done", 99, f"Genie ONNX 已准备并校验：{character_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
