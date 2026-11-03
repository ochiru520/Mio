"""Load original V2 weights' dedicated engines through Anif's streaming runtime.

This is a Mio adapter, not an upstream V2ProPlus model package.  V2 conditions
on its reference spectrogram and has no speaker-verification neural stage.
Importing this module does not import torch/TensorRT or allocate a CUDA context.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys


KIND = "mio-aniflive-v2-experimental"
MODEL_FAMILY = "GPT-SoVITS V2"
ORIGINAL_MIO_WEIGHT_SHA256 = {
    "gpt": "861aa092be98207dd6f61a9fb195fff68995f420c144b3efda68b96c7841ef0f",
    "sovits": "0ecb46028c250621e6084a286679b58dede74a6fb4a0be0f93187a76f559eae7",
}
STAGES = (
    "ssl", "bert", "vq_encoder", "gpt_encoder", "gpt_step",
    "spectrogram", "sovits", "sovits_stream",
)
_ATTRIBUTES = {
    "ssl": "model_ssl", "bert": "model_bert", "vq_encoder": "model_vq",
    "gpt_encoder": "model_gpt_enc", "gpt_step": "model_gpt_step",
    "spectrogram": "model_spectrogram", "sovits": "model_sovits",
    "sovits_stream": "model_sovits_stream",
}
_IO = {
    "ssl": ({"audio"}, {"last_hidden_state"}),
    "bert": ({"input_ids", "attention_mask", "token_type_ids"}, {"hidden_states"}),
    "vq_encoder": ({"ssl_content"}, {"codes"}),
    "gpt_encoder": (
        {"phoneme_ids", "prompts", "bert_feature"},
        {"topk_values", "topk_indices", "k_cache", "v_cache", "x_len", "y_len", "logits"},
    ),
    "gpt_step": (
        {"samples", "k_cache", "v_cache", "x_len", "y_len", "idx"},
        {"topk_values", "topk_indices", "k_cache_new", "v_cache_new", "logits"},
    ),
    "spectrogram": ({"audio"}, {"spectrogram"}),
    "sovits": ({"pred_semantic", "text_seq", "refer_spec", "noise_scale"}, {"audio"}),
    "sovits_stream": (
        {"pred_semantic", "text_seq", "refer_spec", "noise_scale", "result_length",
         "overlap_frames", "overlap_enabled", "acoustic_noise"},
        {"audio", "latent", "latent_mask"},
    ),
}


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_original_mio_weights(weight_sha256):
    """Recognize the actual approved checkpoint pair, never a profile's name."""
    return (isinstance(weight_sha256, dict)
            and set(weight_sha256) == set(ORIGINAL_MIO_WEIGHT_SHA256)
            and all(isinstance(weight_sha256[key], str)
                    and weight_sha256[key].lower() == value
                    for key, value in ORIGINAL_MIO_WEIGHT_SHA256.items()))


def _object_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("V2模型清单必须是JSON对象。")
    return data


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("V2模型清单缺少有效SHA256。")
    return value.lower()


def _asset(record, root):
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise ValueError("V2模型清单缺少文件路径。")
    path = Path(record["path"])
    path = (path if path.is_absolute() else root / path).resolve(strict=True)
    if not path.is_file() or file_sha256(path) != _hash(record.get("sha256")):
        raise ValueError("V2模型文件SHA256与清单不符。")
    return path


def validate_bundle(engine_dir, expected_weight_sha256):
    """Verify model identity and all eight engines before any CUDA import."""
    root = Path(engine_dir).resolve(strict=True)
    manifest = _object_file(root / "v2-manifest.json")
    if manifest.get("kind") != KIND or manifest.get("model_family") != MODEL_FAMILY:
        raise ValueError("需要原Mio V2专用Anif引擎，不能加载V2ProPlus模型包。")
    if not isinstance(expected_weight_sha256, dict) or set(expected_weight_sha256) != {"gpt", "sovits"}:
        raise ValueError("必须明确原GPT和SoVITS两份权重的SHA256。")
    sources = manifest.get("source_weights")
    if not isinstance(sources, dict) or set(sources) != {"gpt", "sovits"}:
        raise ValueError("V2模型清单缺少原权重来源。")
    for name in ("gpt", "sovits"):
        if not isinstance(sources[name], dict) or _hash(sources[name].get("sha256")) != _hash(expected_weight_sha256[name]):
            raise ValueError("V2引擎来源不是所选原Mio权重。")
        _asset(sources[name], root)
    records = manifest.get("stages")
    if not isinstance(records, dict) or set(records) != set(STAGES):
        raise ValueError("原Mio V2必须恰好包含八个阶段，不使用SV神经模型。")
    paths = {name: _asset(records[name], root) for name in STAGES}
    config = _object_file(root / "config.json")
    model, data = config.get("model", {}), config.get("data", {})
    if (not isinstance(model, dict) or model.get("version") != "v2"
            or model.get("semantic_frame_rate") != "25hz"
            or not isinstance(data, dict) or data.get("sampling_rate") != 32000
            or data.get("hop_length") != 640):
        raise ValueError("V2引擎配置不是受支持的32kHz/25Hz原Mio模型。")
    if "config_sha256" in manifest and file_sha256(root / "config.json") != _hash(manifest["config_sha256"]):
        raise ValueError("V2引擎配置SHA256与清单不符。")
    return {"root": root, "manifest": manifest, "paths": paths, "config": config}


def _validate_stage(stage, module, torch):
    required_inputs, required_outputs = _IO[stage]
    inputs, outputs = set(module.input_names), set(module.output_names)
    permitted_inputs = required_inputs | ({"speed"} if stage.startswith("sovits") else set())
    if (len(inputs) != len(module.input_names) or len(outputs) != len(module.output_names)
            or not required_inputs <= inputs or not inputs <= permitted_inputs
            or outputs != required_outputs):
        raise ValueError(f"V2 TensorRT阶段输入输出不符：{stage}。")
    if stage in {"gpt_encoder", "gpt_step"}:
        if (tuple(module.engine.get_tensor_shape("logits")) != (1, 1025)
                or module.tensor_dtype.get("logits") != torch.float32):
            raise ValueError("原Mio采样需要完整1025词表FP32 logits。")
    if stage == "sovits_stream":
        shape = tuple(module.engine.get_tensor_shape("overlap_frames"))
        if len(shape) != 3 or shape[:2] != (1, 192) or shape[-1] <= 0 or shape[-1] % 2:
            raise ValueError("V2流式引擎的重叠帧形状无效。")


class _UnusedSpeakerEmbedding:
    """Structural placeholder for Anif's reference record; never a neural model.

    Both verified V2 acoustic engines reject sv_emb in their input contracts.
    Anif filters this placeholder out before executing either decoder.
    """
    def __init__(self, torch, device, precision):
        self.torch, self.device, self.precision = torch, device, precision

    def __call__(self, _inputs):
        return {"sv_embedding": self.torch.zeros((1, 512), device=self.device, dtype=self.precision)}


def create_engine(*, engine_dir, bert_path, expected_weight_sha256, device="cuda", source_dir=None):
    """Construct an eight-stage V2 engine without calling the Pro-only loader.

    ``source_dir`` optionally names the existing AnifLive source checkout.
    Without it the caller must have configured its src/minimal_inference paths.
    The caller owns request serialization and process/resource guards.
    """
    bundle = validate_bundle(engine_dir, expected_weight_sha256)
    if device != "cuda" and not str(device).startswith("cuda:"):
        raise ValueError("原Mio Anif加速仅支持CUDA设备。")
    if source_dir is not None:
        source_root = Path(source_dir).resolve(strict=True)
        for path in reversed((source_root / "src", source_root / "minimal_inference",
                              source_root / "minimal_inference/GPT_SoVITS")):
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)
    source = importlib.import_module("run_trt_inference")
    if source_dir is not None:
        imported = Path(source.__file__).resolve()
        if source_root not in imported.parents:
            raise ValueError("已导入的Anif运行时不属于所选源码。")
    torch = importlib.import_module("torch")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA不可用，不能加载V2 TensorRT引擎。")
    # Reuse only the multilingual frontend methods, not the nine-stage init or
    # its catch-and-continue warmup, so missing/mixed model stages fail closed.
    engine = object.__new__(source.GPTSoVITS_TRT_Inference)
    engine.trt_dir = str(bundle["root"])
    engine.device = torch.device(device)
    engine.stream = torch.cuda.Stream(device=engine.device)
    engine.hps = bundle["config"]
    engine.version = "v2"
    engine.shape_profiler = source.ShapeProfiler()
    for stage in STAGES:
        module = source.TRTModule(str(bundle["paths"][stage]), engine.device, engine.stream)
        _validate_stage(stage, module, torch)
        setattr(engine, _ATTRIBUTES[stage], module)
    engine.precision = engine.model_gpt_enc.tensor_dtype["bert_feature"]
    engine.model_sv_embedding = _UnusedSpeakerEmbedding(torch, engine.device, engine.precision)
    transformers = importlib.import_module("transformers")
    engine.tokenizer = transformers.AutoTokenizer.from_pretrained(
        str(Path(bert_path).resolve(strict=True)), local_files_only=True, trust_remote_code=False,
    )
    for suffix, name in (("sem", "pred_semantic"), ("text", "text_seq"), ("ref", "refer_spec")):
        shape = engine.model_sovits.input_max_shapes.get(name)
        setattr(engine, f"sovits_max_{suffix}_len", int(shape[-1]) if shape else None)
    engine.v2_metadata = {
        "engine": "aniflive", "runtime": "aniflive_v2", "model_family": MODEL_FAMILY,
        "model_version": MODEL_FAMILY, "uses_original_finetuned_weights": True,
        "original_mio_weights_loaded": is_original_mio_weights(expected_weight_sha256),
        "uses_speaker_embedding": False, "neural_stage_count": 8,
        "weight_sha256": {key: _hash(expected_weight_sha256[key]) for key in ("gpt", "sovits")},
        "active_weights": {key: str(_asset(bundle["manifest"]["source_weights"][key], bundle["root"]))
                           for key in ("gpt", "sovits")},
        "backend": "TensorRT-11", "experimental": True, "numerical_verified": False,
    }
    return engine


@contextmanager
def _construction_environment():
    # The existing upstream contract's name says ProPlus, but its full-logit
    # repetition-penalty/sampling algorithm is also GPT-SoVITS V2's algorithm.
    updates = {"ANIFLIVE_TTS_SEMANTIC_BACKEND": "transformer",
               "ANIFLIVE_TTS_SEMANTIC_SAMPLING": "native-v2proplus-v1"}
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def create_streamer(engine, *, reference_wav, reference_text, reference_language="ja", logger=None):
    """Create Anif's actual streamer with V2 decoders; caller prepares reference."""
    if getattr(engine, "version", None) != "v2" or not hasattr(engine, "v2_metadata"):
        raise ValueError("需要已校验原权重的V2专用引擎。")
    if reference_language not in {"zh", "ja", "en", "yue", "ko"} or not reference_text.strip():
        raise ValueError("参考音频语言或文本无效。")
    reference = Path(reference_wav).resolve(strict=True)
    if not reference.is_file():
        raise ValueError("参考音频必须是本地文件。")
    streaming = importlib.import_module("aniflive_tts.streaming")
    with _construction_environment():
        streamer = streaming.TensorRTFixedReferenceStreamer(
            engine, reference_wav=reference, reference_text=reference_text,
            reference_language=reference_language, logger=logger,
        )
    if streamer.semantic_runtime.sampling_contract != "native-v2proplus-v1":
        streamer.close()
        raise RuntimeError("未启用原V2完整词表采样。")
    return streamer
