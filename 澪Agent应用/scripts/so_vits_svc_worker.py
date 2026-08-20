from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
import traceback


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _prepare_imports():
    runtime_dir = Path(os.environ["MIO_SO_VITS_SVC_DIR"]).resolve()
    dependency_dir = Path(os.environ["MIO_SO_VITS_SVC_SITE"]).resolve()
    for candidate in (
        dependency_dir,
        dependency_dir / "win32",
        dependency_dir / "win32" / "lib",
        dependency_dir / "pywin32_system32",
        runtime_dir,
    ):
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
    os.chdir(runtime_dir)

    import torch

    original_load = torch.load

    def trusted_checkpoint_load(*args, **kwargs):
        # So-VITS-SVC 4.1 and fairseq predate PyTorch 2.6's weights_only default.
        kwargs.setdefault("weights_only", False)
        return original_load(*args, **kwargs)

    torch.load = trusted_checkpoint_load
    with contextlib.redirect_stdout(sys.stderr):
        from inference.infer_tool import Svc
        import soundfile
    return Svc, soundfile


def main() -> int:
    try:
        svc_type, soundfile = _prepare_imports()
    except Exception as exc:
        _emit({"ok": False, "fatal": True, "error": f"So-VITS-SVC 运行环境加载失败：{exc}"})
        return 1

    model = None
    model_key: tuple[str, str, str] | None = None
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            action = str(request.get("action") or "")
            if action == "shutdown":
                _emit({"ok": True})
                return 0
            if action not in {"probe", "convert"}:
                raise ValueError("未知的 So-VITS-SVC Worker 操作。")

            requested_key = (
                str(Path(request["model_path"]).resolve()),
                str(Path(request["config_path"]).resolve()),
                str(request.get("device") or "cuda"),
            )
            if model is None or requested_key != model_key:
                if model is not None:
                    with contextlib.suppress(Exception):
                        model.unload_model()
                with contextlib.redirect_stdout(sys.stderr):
                    model = svc_type(
                        requested_key[0],
                        requested_key[1],
                        device=requested_key[2],
                        cluster_model_path="",
                    )
                model_key = requested_key

            speaker = str(request.get("speaker") or "").strip()
            if speaker not in model.spk2id:
                raise ValueError(f"模型中找不到说话人：{speaker}")
            if action == "probe":
                _emit({"ok": True, "sample_rate": int(model.target_sample), "speakers": list(model.spk2id.keys())})
                continue

            input_path = Path(request["input_path"]).resolve()
            output_path = Path(request["output_path"]).resolve()
            if not input_path.is_file():
                raise ValueError("基础语音文件不存在。")
            with contextlib.redirect_stdout(sys.stderr):
                audio = model.slice_inference(
                    raw_audio_path=str(input_path),
                    spk=speaker,
                    tran=max(-24, min(24, int(request.get("pitch") or 0))),
                    slice_db=-40,
                    cluster_infer_ratio=0,
                    auto_predict_f0=bool(request.get("auto_predict_f0", True)),
                    noice_scale=max(0.0, min(1.0, float(request.get("noise_scale") or 0.4))),
                    pad_seconds=0.5,
                    f0_predictor="pm",
                )
                soundfile.write(output_path, audio, model.target_sample, format="WAV", subtype="PCM_16")
                model.clear_empty()
            _emit({"ok": True, "sample_rate": int(model.target_sample), "samples": int(len(audio))})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            _emit({"ok": False, "error": str(exc)[:1000]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
